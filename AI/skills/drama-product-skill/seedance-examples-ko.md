---
name: seedance-examples-ko
description: "This skill should be used when the user asks for Korean Seedance 2.0 examples, Korean prompt patterns, example rewrites, or safe versions of working Korean video-generation prompts."
---

# seedance-examples-ko

Do not write `@Image1`, `@图片1`, `@Video1`, or `@Audio1` into a shot prompt. Put an upload id in the asset `references` array. A shot's `images` lists only generated asset ids. One reference does one job: a portrait locks the person, a product still locks the product, and the two do not cross.

Before producing prompt text, a prompt-ready block, a rewrite, an example, or a compiled clip, read `seedance-directors-read.md`, classify the brief, and complete its canonical narrative or non-narrative record. Translate that record into visible or audible carriers. Internal labels such as POV, power shift, and subtext may stay in the brief. They do not appear in `shots[].prompt`.

Use Korean examples as language-specific prompt patterns, not translated English templates.

## Intent

Korean examples should turn 감성 into production behavior: the feeling is carried by shot size, light direction, stillness, small gestures, room tone, and post-production text handling. Keep the prompt compact enough to direct the clip, not narrate the whole drama.

## Example Labels

| Label | Meaning |
|---|---|
| `safe` | Original concept, no protected identity. |
| `needs-owned-reference` | Requires user-owned, licensed, public-domain, or authorized asset. |
| `surface-specific` | Depends on the active web, API, or workflow surface. |
| `rewrite-required` | Mentions protected identity, brand, celebrity, exact scene, song, or voice. |

## Safe Example Patterns

**Product I2V:** `병 모양과 색은 업로드를 따르고, 상표 글자는 그리지 않는다. 변화는 작은 물방울이 표면을 따라 내려가는 움직임과 왼쪽에서 지나가는 따뜻한 프랙티컬 조명만 적용한다. Camera: locked product close-up, slow push-in. Sound: 낮은 실내 환경음, 마지막에 작은 유리 소리.`

**Melodrama micro-expression:** `현대 아파트 주방, 두 명의 original adult characters only. Character A lowers a ceramic mug and looks away; Character B stays near the window, no approach. Camera: locked medium-wide, subtle handheld breathing sway. Lighting: warm tungsten practical, faint blue city spill. Sound: refrigerator hum, fabric movement, no music.`

**Sequence clip 01:** `오리지널 인물A가 새벽 버스 정류장에 도착한다. 이번 클립의 역할은 기다림의 이유를 암시하는 것뿐이다. A는 접힌 메모를 발견하고 손을 뻗기 직전에 멈춘다. Camera: stable lateral tracking, medium-wide. 이 클립에서는 만남이나 버스 출발을 보여주지 않는다.`

**Continuation:** `이전 승인된 클립의 끝 상태에서 이어진다. A는 메모 앞에서 멈춘 상태로 시작하고, 천천히 메모를 집어 들고 멀리서 들리는 안내음에 고개를 든다. 이전의 도착 동작은 반복하지 않는다. Camera: locked medium shot, slight push-in.`

**Dialogue:** `Character A sits at a cafe table, locked medium close-up, shoulders still. She says, "괜찮아, 천천히 말해." 대사 중에는 고개를 돌리지 않고 작은 입 움직임만 사용한다. Sound: clear short dialogue, soft cafe room tone, no music under the line.`

**Textless localization:** `세로 화면. 화폭은 JSON 필드에 두고 샷 본문에는 쓰지 않는다. 제품은 중앙에 유지하고 가장자리에는 중요한 동작을 두지 않는다. 생성 자막, 워터마크, 광고 문구를 화면 안에 넣지 않는다.`

## Rewrite Pattern

If the prompt contains protected names, rewrite the creative function into original Korean descriptors: `유명 캐릭터 그대로` becomes `오리지널 마스크를 쓴 옥상 배달원`; `특정 작품과 똑같이` becomes `낮은 채도 야경, 강한 사이드 라이트, 조용한 표정 연기, 망원 압축감`.

## Output Contract

Return the Korean example, label, risk note, and safer Korean variant when needed. Put that prose into `script` and `shots[].prompt`. The delivery shape is the JSON in `写提示词.md`.
