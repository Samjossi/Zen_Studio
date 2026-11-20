# Noto 彩色 Emoji 图标目录（卡片图标挑选用）

> **用途**：聊天卡片标题图标的候选池。全部来自项目自带 Noto Color Emoji，不引入外部图标库。
> **字体**：`assets/fonts/Noto彩色Emoji/NotoColorEmoji.ttf`
> **字体 sha256**：`72a635cb3d2f3524c51620cdde406b217204e8a6a06c6a096ff8ed4b5fd6e27b`
> **枚举口径**：字体 charset 共 1,499 个码点，筛出单码点可独立上屏的 1,395 个。
> **不列入**：ZWJ 组合、肤色修饰、区域旗帜、键帽、标签字符；变体选择符不单列，但 `U+1F441` 的实际显示值必须附加 `U+FE0F`（见 §1.2）。
> **更新日期**：2026-11-21
> **相关计划**：`work plans/2026-1121-0200_DSH卡片图标分型与read_image图片卡改造计划.md`

---

## 1. 怎么用

- 在编辑器里搜索 `U+1F5BC`、英文名称或本文档的章节名；emoji 列可直接复制。
- `卡片标记` 列标出：`现行` = 已被卡片代码使用；`建议` = DSH 计划推荐；`备选` = 同族备选。
- 编辑器/聊天窗口渲染 emoji 的字体可能不是 Noto；app 内固定使用 Noto Color Emoji，最终以 app 实际截图为准。
- 个别新码点在旧 Qt/字体组合下可能显示为方框；选中后先在 app 10pt 图标列里实测再定。
- 卡片标题图标列宽 20px、app 默认字号 10pt；优先选轮廓清楚、缩小后不糊的字形。
- `👁️` 是 `U+1F441 + U+FE0F` 两码点：`U+1F441` 默认文本呈现，不加 VS16 在 10pt 浅色卡头会退成灰黑文本眼；这是本目录唯一的 VS16 例外，代码里请写 `"\U0001F441\uFE0F"` 防丢。
- 找卡片图标优先看 `3.9 物件与办公`、`3.10 物件与生活`、`3.15 工具、物件与生活`；符号段里的 ©/®/™/箭头可能偏文本风格。
- 选好后直接回复码点或名称，例如 `read_image = U+1F441 U+FE0F`；需要换某张卡时同样按「卡片 = 码点」写。

### 1.1 现行图标（原范围）

| 用途 | 图标 | 码点 | Unicode 名称 |
|:---|:---:|:---:|:---|
| execute | 💻 | U+1F4BB | PERSONAL COMPUTER |
| edit | 📝 | U+1F4DD | MEMO |
| read | 📄 | U+1F4C4 | PAGE FACING UP |
| search | 🔍 | U+1F50D | LEFT-POINTING MAGNIFYING GLASS |
| fetch | 🌐 | U+1F310 | GLOBE WITH MERIDIANS |
| think | 🤖 | U+1F916 | ROBOT FACE |
| other | 🧩 | U+1F9E9 | JIGSAW PUZZLE PIECE |
| Thinking | 🧠 | U+1F9E0 | BRAIN |
| TodoCard | 📋 | U+1F4CB | CLIPBOARD |
| 问答正文 | ❓ | U+2753 | BLACK QUESTION MARK ORNAMENT |
| 答案 | ✅ | U+2705 | WHITE HEAVY CHECK MARK |
| todo 完成 | ☑ | U+2611 | BALLOT BOX WITH CHECK |
| todo 进行中 | ▶ | U+25B6 | BLACK RIGHT-POINTING TRIANGLE |
| 跳过 | ⏭ | U+23ED | BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR |

状态/折叠/复制另有一组文本符号，不属于 emoji 族：`◐` U+25D0、`✔` U+2714、`✖` U+2716、`▸` U+25B8、`▾` U+25BE、`⧉` U+29C9、`✓` U+2713。其中 `◐`、`☐` U+2610、`▸`、`▾`、`⧉`、`✓` 不在本字体 charset，由主字体渲染，所以不出现在下方目录。

### 1.2 DSH 卡型候选

| 卡片类型 | 建议 | 码点 | 备选 | 备选码点 | 10pt 备注 |
|:---|:---:|:---:|:---:|:---:|:---|
| `read_image` | 👁️ | U+1F441 U+FE0F | 👀 / 🖼 | U+1F440 / U+1F5BC | VS16 必需；不加会退成灰黑文本眼；宽度与单码点相同 |
| `todo_write` | 🎯 | U+1F3AF | 🗒 | U+1F5D2 | 红白靶心，10pt 醒目；语义偏「目标」；📋 留给 TodoCard，不共用 |
| `TodoCard` 计划卡 | 📋 | U+1F4CB | 📑 / 📌 | U+1F4D1 / U+1F4CC | 保持现行图标，语义最准，三家一致 |
| MCP 服务器 | 🔌 | U+1F50C | 🧰 / 🧩 | U+1F9F0 / U+1F9E9 | 只给 `mcp__` 前缀；未知/AskUserQuestion 保持 🧩 |
| `QuestionCard` 预留 | ❓ | U+2753 | — | — | 红色醒目；当前 DSH 的 AskUserQuestion 仍走 McpCard 🧩 |

> 以上为用户 2026-11-21 确认的组合；`📑`/`📌`/`🗒`/`🖼`/`👀` 只作备选。
> `📋` 是 `TodoCard` 的现行图标，本次保持不改；`todo_write` 用 🎯，两者不再共用 📋。MCP 的 🔌 必须由工厂只对 `mcp__` 前缀覆盖，不能把 `McpCard` 全局设为 🔌，否则未知工具和 AskUserQuestion 都会变插头。

---

## 2. 分类索引

| 章节 | 范围 | 数量 |
|:---|:---|:---:|
| [3.1 符号、箭头与几何](#sec-1) | U+00A9–U+2BFF | 170 |
| [3.2 牌类与棋类](#sec-2) | U+1F000–U+1F0FF | 2 |
| [3.3 封闭字母与补充符号](#sec-3) | U+1F100–U+1F2FF | 30 |
| [3.4 自然、天气与动物](#sec-4) | U+1F300–U+1F32C | 43 |
| [3.5 食物与饮料](#sec-5) | U+1F32D–U+1F37F | 83 |
| [3.6 活动、运动与地点](#sec-6) | U+1F380–U+1F3FF | 115 |
| [3.7 动物与自然](#sec-7) | U+1F400–U+1F43F | 64 |
| [3.8 表情、人物与手势](#sec-8) | U+1F440–U+1F49F | 96 |
| [3.9 物件与办公](#sec-9) | U+1F4A0–U+1F4FF | 95 |
| [3.10 物件与生活](#sec-10) | U+1F500–U+1F5FF | 136 |
| [3.11 表情与人物](#sec-11) | U+1F600–U+1F64F | 80 |
| [3.12 交通与地点](#sec-12) | U+1F680–U+1F6FF | 106 |
| [3.13 补充符号](#sec-13) | U+1F700–U+1F8FF | 13 |
| [3.14 手势、人物与运动](#sec-14) | U+1F900–U+1F946 | 57 |
| [3.15 工具、物件与生活](#sec-15) | U+1F947–U+1F9FF | 185 |
| [3.16 新增物件与符号](#sec-16) | U+1FA00–U+1FAFF | 120 |

---

## 3. 全量目录

> 共 1,395 个。`#` 为按码点排序的全局序号，跨分区连续。

<a id="sec-1"></a>
### 3.1 符号、箭头与几何（U+00A9–U+2BFF，170 个）

| # | 图标 | 码点 | Unicode 名称 | 卡片标记 |
|:---:|:---:|:---:|:---|:---|
| 1 | © | U+00A9 | COPYRIGHT SIGN |  |
| 2 | ® | U+00AE | REGISTERED SIGN |  |
| 3 | ‼ | U+203C | DOUBLE EXCLAMATION MARK |  |
| 4 | ⁉ | U+2049 | EXCLAMATION QUESTION MARK |  |
| 5 | ™ | U+2122 | TRADE MARK SIGN |  |
| 6 | ℹ | U+2139 | INFORMATION SOURCE |  |
| 7 | ↔ | U+2194 | LEFT RIGHT ARROW |  |
| 8 | ↕ | U+2195 | UP DOWN ARROW |  |
| 9 | ↖ | U+2196 | NORTH WEST ARROW |  |
| 10 | ↗ | U+2197 | NORTH EAST ARROW |  |
| 11 | ↘ | U+2198 | SOUTH EAST ARROW |  |
| 12 | ↙ | U+2199 | SOUTH WEST ARROW |  |
| 13 | ↩ | U+21A9 | LEFTWARDS ARROW WITH HOOK |  |
| 14 | ↪ | U+21AA | RIGHTWARDS ARROW WITH HOOK |  |
| 15 | ⌚ | U+231A | WATCH |  |
| 16 | ⌛ | U+231B | HOURGLASS |  |
| 17 | ⌨ | U+2328 | KEYBOARD |  |
| 18 | ⏏ | U+23CF | EJECT SYMBOL |  |
| 19 | ⏩ | U+23E9 | BLACK RIGHT-POINTING DOUBLE TRIANGLE |  |
| 20 | ⏪ | U+23EA | BLACK LEFT-POINTING DOUBLE TRIANGLE |  |
| 21 | ⏫ | U+23EB | BLACK UP-POINTING DOUBLE TRIANGLE |  |
| 22 | ⏬ | U+23EC | BLACK DOWN-POINTING DOUBLE TRIANGLE |  |
| 23 | ⏭ | U+23ED | BLACK RIGHT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR | 现行 · 跳过 |
| 24 | ⏮ | U+23EE | BLACK LEFT-POINTING DOUBLE TRIANGLE WITH VERTICAL BAR |  |
| 25 | ⏯ | U+23EF | BLACK RIGHT-POINTING TRIANGLE WITH DOUBLE VERTICAL BAR |  |
| 26 | ⏰ | U+23F0 | ALARM CLOCK |  |
| 27 | ⏱ | U+23F1 | STOPWATCH |  |
| 28 | ⏲ | U+23F2 | TIMER CLOCK |  |
| 29 | ⏳ | U+23F3 | HOURGLASS WITH FLOWING SAND |  |
| 30 | ⏸ | U+23F8 | DOUBLE VERTICAL BAR |  |
| 31 | ⏹ | U+23F9 | BLACK SQUARE FOR STOP |  |
| 32 | ⏺ | U+23FA | BLACK CIRCLE FOR RECORD |  |
| 33 | Ⓜ | U+24C2 | CIRCLED LATIN CAPITAL LETTER M |  |
| 34 | ▪ | U+25AA | BLACK SMALL SQUARE |  |
| 35 | ▫ | U+25AB | WHITE SMALL SQUARE |  |
| 36 | ▶ | U+25B6 | BLACK RIGHT-POINTING TRIANGLE | 现行 · todo 进行中 |
| 37 | ◀ | U+25C0 | BLACK LEFT-POINTING TRIANGLE |  |
| 38 | ◻ | U+25FB | WHITE MEDIUM SQUARE |  |
| 39 | ◼ | U+25FC | BLACK MEDIUM SQUARE |  |
| 40 | ◽ | U+25FD | WHITE MEDIUM SMALL SQUARE |  |
| 41 | ◾ | U+25FE | BLACK MEDIUM SMALL SQUARE |  |
| 42 | ☀ | U+2600 | BLACK SUN WITH RAYS |  |
| 43 | ☁ | U+2601 | CLOUD |  |
| 44 | ☂ | U+2602 | UMBRELLA |  |
| 45 | ☃ | U+2603 | SNOWMAN |  |
| 46 | ☄ | U+2604 | COMET |  |
| 47 | ☎ | U+260E | BLACK TELEPHONE |  |
| 48 | ☑ | U+2611 | BALLOT BOX WITH CHECK | 现行 · todo 完成 |
| 49 | ☔ | U+2614 | UMBRELLA WITH RAIN DROPS |  |
| 50 | ☕ | U+2615 | HOT BEVERAGE |  |
| 51 | ☘ | U+2618 | SHAMROCK |  |
| 52 | ☝ | U+261D | WHITE UP POINTING INDEX |  |
| 53 | ☠ | U+2620 | SKULL AND CROSSBONES |  |
| 54 | ☢ | U+2622 | RADIOACTIVE SIGN |  |
| 55 | ☣ | U+2623 | BIOHAZARD SIGN |  |
| 56 | ☦ | U+2626 | ORTHODOX CROSS |  |
| 57 | ☪ | U+262A | STAR AND CRESCENT |  |
| 58 | ☮ | U+262E | PEACE SYMBOL |  |
| 59 | ☯ | U+262F | YIN YANG |  |
| 60 | ☸ | U+2638 | WHEEL OF DHARMA |  |
| 61 | ☹ | U+2639 | WHITE FROWNING FACE |  |
| 62 | ☺ | U+263A | WHITE SMILING FACE |  |
| 63 | ♀ | U+2640 | FEMALE SIGN |  |
| 64 | ♂ | U+2642 | MALE SIGN |  |
| 65 | ♈ | U+2648 | ARIES |  |
| 66 | ♉ | U+2649 | TAURUS |  |
| 67 | ♊ | U+264A | GEMINI |  |
| 68 | ♋ | U+264B | CANCER |  |
| 69 | ♌ | U+264C | LEO |  |
| 70 | ♍ | U+264D | VIRGO |  |
| 71 | ♎ | U+264E | LIBRA |  |
| 72 | ♏ | U+264F | SCORPIUS |  |
| 73 | ♐ | U+2650 | SAGITTARIUS |  |
| 74 | ♑ | U+2651 | CAPRICORN |  |
| 75 | ♒ | U+2652 | AQUARIUS |  |
| 76 | ♓ | U+2653 | PISCES |  |
| 77 | ♟ | U+265F | BLACK CHESS PAWN |  |
| 78 | ♠ | U+2660 | BLACK SPADE SUIT |  |
| 79 | ♣ | U+2663 | BLACK CLUB SUIT |  |
| 80 | ♥ | U+2665 | BLACK HEART SUIT |  |
| 81 | ♦ | U+2666 | BLACK DIAMOND SUIT |  |
| 82 | ♨ | U+2668 | HOT SPRINGS |  |
| 83 | ♻ | U+267B | BLACK UNIVERSAL RECYCLING SYMBOL |  |
| 84 | ♾ | U+267E | PERMANENT PAPER SIGN |  |
| 85 | ♿ | U+267F | WHEELCHAIR SYMBOL |  |
| 86 | ⚒ | U+2692 | HAMMER AND PICK |  |
| 87 | ⚓ | U+2693 | ANCHOR |  |
| 88 | ⚔ | U+2694 | CROSSED SWORDS |  |
| 89 | ⚕ | U+2695 | STAFF OF AESCULAPIUS |  |
| 90 | ⚖ | U+2696 | SCALES |  |
| 91 | ⚗ | U+2697 | ALEMBIC |  |
| 92 | ⚙ | U+2699 | GEAR |  |
| 93 | ⚛ | U+269B | ATOM SYMBOL |  |
| 94 | ⚜ | U+269C | FLEUR-DE-LIS |  |
| 95 | ⚠ | U+26A0 | WARNING SIGN |  |
| 96 | ⚡ | U+26A1 | HIGH VOLTAGE SIGN |  |
| 97 | ⚧ | U+26A7 | MALE WITH STROKE AND MALE AND FEMALE SIGN |  |
| 98 | ⚪ | U+26AA | MEDIUM WHITE CIRCLE |  |
| 99 | ⚫ | U+26AB | MEDIUM BLACK CIRCLE |  |
| 100 | ⚰ | U+26B0 | COFFIN |  |
| 101 | ⚱ | U+26B1 | FUNERAL URN |  |
| 102 | ⚽ | U+26BD | SOCCER BALL |  |
| 103 | ⚾ | U+26BE | BASEBALL |  |
| 104 | ⛄ | U+26C4 | SNOWMAN WITHOUT SNOW |  |
| 105 | ⛅ | U+26C5 | SUN BEHIND CLOUD |  |
| 106 | ⛈ | U+26C8 | THUNDER CLOUD AND RAIN |  |
| 107 | ⛎ | U+26CE | OPHIUCHUS |  |
| 108 | ⛏ | U+26CF | PICK |  |
| 109 | ⛑ | U+26D1 | HELMET WITH WHITE CROSS |  |
| 110 | ⛓ | U+26D3 | CHAINS |  |
| 111 | ⛔ | U+26D4 | NO ENTRY |  |
| 112 | ⛩ | U+26E9 | SHINTO SHRINE |  |
| 113 | ⛪ | U+26EA | CHURCH |  |
| 114 | ⛰ | U+26F0 | MOUNTAIN |  |
| 115 | ⛱ | U+26F1 | UMBRELLA ON GROUND |  |
| 116 | ⛲ | U+26F2 | FOUNTAIN |  |
| 117 | ⛳ | U+26F3 | FLAG IN HOLE |  |
| 118 | ⛴ | U+26F4 | FERRY |  |
| 119 | ⛵ | U+26F5 | SAILBOAT |  |
| 120 | ⛷ | U+26F7 | SKIER |  |
| 121 | ⛸ | U+26F8 | ICE SKATE |  |
| 122 | ⛹ | U+26F9 | PERSON WITH BALL |  |
| 123 | ⛺ | U+26FA | TENT |  |
| 124 | ⛽ | U+26FD | FUEL PUMP |  |
| 125 | ✂ | U+2702 | BLACK SCISSORS |  |
| 126 | ✅ | U+2705 | WHITE HEAVY CHECK MARK | 现行 · 答案 |
| 127 | ✈ | U+2708 | AIRPLANE |  |
| 128 | ✉ | U+2709 | ENVELOPE |  |
| 129 | ✊ | U+270A | RAISED FIST |  |
| 130 | ✋ | U+270B | RAISED HAND |  |
| 131 | ✌ | U+270C | VICTORY HAND |  |
| 132 | ✍ | U+270D | WRITING HAND |  |
| 133 | ✏ | U+270F | PENCIL |  |
| 134 | ✒ | U+2712 | BLACK NIB |  |
| 135 | ✔ | U+2714 | HEAVY CHECK MARK | 现行 · 状态完成 |
| 136 | ✖ | U+2716 | HEAVY MULTIPLICATION X | 现行 · 状态失败 |
| 137 | ✝ | U+271D | LATIN CROSS |  |
| 138 | ✡ | U+2721 | STAR OF DAVID |  |
| 139 | ✨ | U+2728 | SPARKLES |  |
| 140 | ✳ | U+2733 | EIGHT SPOKED ASTERISK |  |
| 141 | ✴ | U+2734 | EIGHT POINTED BLACK STAR |  |
| 142 | ❄ | U+2744 | SNOWFLAKE |  |
| 143 | ❇ | U+2747 | SPARKLE |  |
| 144 | ❌ | U+274C | CROSS MARK |  |
| 145 | ❎ | U+274E | NEGATIVE SQUARED CROSS MARK |  |
| 146 | ❓ | U+2753 | BLACK QUESTION MARK ORNAMENT | 现行 · 问答正文 / 建议 · QuestionCard 预留 |
| 147 | ❔ | U+2754 | WHITE QUESTION MARK ORNAMENT |  |
| 148 | ❕ | U+2755 | WHITE EXCLAMATION MARK ORNAMENT |  |
| 149 | ❗ | U+2757 | HEAVY EXCLAMATION MARK SYMBOL |  |
| 150 | ❣ | U+2763 | HEAVY HEART EXCLAMATION MARK ORNAMENT |  |
| 151 | ❤ | U+2764 | HEAVY BLACK HEART |  |
| 152 | ➕ | U+2795 | HEAVY PLUS SIGN |  |
| 153 | ➖ | U+2796 | HEAVY MINUS SIGN |  |
| 154 | ➗ | U+2797 | HEAVY DIVISION SIGN |  |
| 155 | ➡ | U+27A1 | BLACK RIGHTWARDS ARROW |  |
| 156 | ➰ | U+27B0 | CURLY LOOP |  |
| 157 | ➿ | U+27BF | DOUBLE CURLY LOOP |  |
| 158 | ⤴ | U+2934 | ARROW POINTING RIGHTWARDS THEN CURVING UPWARDS |  |
| 159 | ⤵ | U+2935 | ARROW POINTING RIGHTWARDS THEN CURVING DOWNWARDS |  |
| 160 | ⬅ | U+2B05 | LEFTWARDS BLACK ARROW |  |
| 161 | ⬆ | U+2B06 | UPWARDS BLACK ARROW |  |
| 162 | ⬇ | U+2B07 | DOWNWARDS BLACK ARROW |  |
| 163 | ⬛ | U+2B1B | BLACK LARGE SQUARE |  |
| 164 | ⬜ | U+2B1C | WHITE LARGE SQUARE |  |
| 165 | ⭐ | U+2B50 | WHITE MEDIUM STAR |  |
| 166 | ⭕ | U+2B55 | HEAVY LARGE CIRCLE |  |
| 167 | 〰 | U+3030 | WAVY DASH |  |
| 168 | 〽 | U+303D | PART ALTERNATION MARK |  |
| 169 | ㊗ | U+3297 | CIRCLED IDEOGRAPH CONGRATULATION |  |
| 170 | ㊙ | U+3299 | CIRCLED IDEOGRAPH SECRET |  |

<a id="sec-2"></a>
### 3.2 牌类与棋类（U+1F000–U+1F0FF，2 个）

| # | 图标 | 码点 | Unicode 名称 | 卡片标记 |
|:---:|:---:|:---:|:---|:---|
| 171 | 🀄 | U+1F004 | MAHJONG TILE RED DRAGON |  |
| 172 | 🃏 | U+1F0CF | PLAYING CARD BLACK JOKER |  |

<a id="sec-3"></a>
### 3.3 封闭字母与补充符号（U+1F100–U+1F2FF，30 个）

| # | 图标 | 码点 | Unicode 名称 | 卡片标记 |
|:---:|:---:|:---:|:---|:---|
| 173 | 🅰 | U+1F170 | NEGATIVE SQUARED LATIN CAPITAL LETTER A |  |
| 174 | 🅱 | U+1F171 | NEGATIVE SQUARED LATIN CAPITAL LETTER B |  |
| 175 | 🅾 | U+1F17E | NEGATIVE SQUARED LATIN CAPITAL LETTER O |  |
| 176 | 🅿 | U+1F17F | NEGATIVE SQUARED LATIN CAPITAL LETTER P |  |
| 177 | 🆎 | U+1F18E | NEGATIVE SQUARED AB |  |
| 178 | 🆑 | U+1F191 | SQUARED CL |  |
| 179 | 🆒 | U+1F192 | SQUARED COOL |  |
| 180 | 🆓 | U+1F193 | SQUARED FREE |  |
| 181 | 🆔 | U+1F194 | SQUARED ID |  |
| 182 | 🆕 | U+1F195 | SQUARED NEW |  |
| 183 | 🆖 | U+1F196 | SQUARED NG |  |
| 184 | 🆗 | U+1F197 | SQUARED OK |  |
| 185 | 🆘 | U+1F198 | SQUARED SOS |  |
| 186 | 🆙 | U+1F199 | SQUARED UP WITH EXCLAMATION MARK |  |
| 187 | 🆚 | U+1F19A | SQUARED VS |  |
| 188 | 🈁 | U+1F201 | SQUARED KATAKANA KOKO |  |
| 189 | 🈂 | U+1F202 | SQUARED KATAKANA SA |  |
| 190 | 🈚 | U+1F21A | SQUARED CJK UNIFIED IDEOGRAPH-7121 |  |
| 191 | 🈯 | U+1F22F | SQUARED CJK UNIFIED IDEOGRAPH-6307 |  |
| 192 | 🈲 | U+1F232 | SQUARED CJK UNIFIED IDEOGRAPH-7981 |  |
| 193 | 🈳 | U+1F233 | SQUARED CJK UNIFIED IDEOGRAPH-7A7A |  |
| 194 | 🈴 | U+1F234 | SQUARED CJK UNIFIED IDEOGRAPH-5408 |  |
| 195 | 🈵 | U+1F235 | SQUARED CJK UNIFIED IDEOGRAPH-6E80 |  |
| 196 | 🈶 | U+1F236 | SQUARED CJK UNIFIED IDEOGRAPH-6709 |  |
| 197 | 🈷 | U+1F237 | SQUARED CJK UNIFIED IDEOGRAPH-6708 |  |
| 198 | 🈸 | U+1F238 | SQUARED CJK UNIFIED IDEOGRAPH-7533 |  |
| 199 | 🈹 | U+1F239 | SQUARED CJK UNIFIED IDEOGRAPH-5272 |  |
| 200 | 🈺 | U+1F23A | SQUARED CJK UNIFIED IDEOGRAPH-55B6 |  |
| 201 | 🉐 | U+1F250 | CIRCLED IDEOGRAPH ADVANTAGE |  |
| 202 | 🉑 | U+1F251 | CIRCLED IDEOGRAPH ACCEPT |  |

<a id="sec-4"></a>
### 3.4 自然、天气与动物（U+1F300–U+1F32C，43 个）

| # | 图标 | 码点 | Unicode 名称 | 卡片标记 |
|:---:|:---:|:---:|:---|:---|
| 203 | 🌀 | U+1F300 | CYCLONE |  |
| 204 | 🌁 | U+1F301 | FOGGY |  |
| 205 | 🌂 | U+1F302 | CLOSED UMBRELLA |  |
| 206 | 🌃 | U+1F303 | NIGHT WITH STARS |  |
| 207 | 🌄 | U+1F304 | SUNRISE OVER MOUNTAINS |  |
| 208 | 🌅 | U+1F305 | SUNRISE |  |
| 209 | 🌆 | U+1F306 | CITYSCAPE AT DUSK |  |
| 210 | 🌇 | U+1F307 | SUNSET OVER BUILDINGS |  |
| 211 | 🌈 | U+1F308 | RAINBOW |  |
| 212 | 🌉 | U+1F309 | BRIDGE AT NIGHT |  |
| 213 | 🌊 | U+1F30A | WATER WAVE |  |
| 214 | 🌋 | U+1F30B | VOLCANO |  |
| 215 | 🌌 | U+1F30C | MILKY WAY |  |
| 216 | 🌍 | U+1F30D | EARTH GLOBE EUROPE-AFRICA |  |
| 217 | 🌎 | U+1F30E | EARTH GLOBE AMERICAS |  |
| 218 | 🌏 | U+1F30F | EARTH GLOBE ASIA-AUSTRALIA |  |
| 219 | 🌐 | U+1F310 | GLOBE WITH MERIDIANS | 现行 · fetch |
| 220 | 🌑 | U+1F311 | NEW MOON SYMBOL |  |
| 221 | 🌒 | U+1F312 | WAXING CRESCENT MOON SYMBOL |  |
| 222 | 🌓 | U+1F313 | FIRST QUARTER MOON SYMBOL |  |
| 223 | 🌔 | U+1F314 | WAXING GIBBOUS MOON SYMBOL |  |
| 224 | 🌕 | U+1F315 | FULL MOON SYMBOL |  |
| 225 | 🌖 | U+1F316 | WANING GIBBOUS MOON SYMBOL |  |
| 226 | 🌗 | U+1F317 | LAST QUARTER MOON SYMBOL |  |
| 227 | 🌘 | U+1F318 | WANING CRESCENT MOON SYMBOL |  |
| 228 | 🌙 | U+1F319 | CRESCENT MOON |  |
| 229 | 🌚 | U+1F31A | NEW MOON WITH FACE |  |
| 230 | 🌛 | U+1F31B | FIRST QUARTER MOON WITH FACE |  |
| 231 | 🌜 | U+1F31C | LAST QUARTER MOON WITH FACE |  |
| 232 | 🌝 | U+1F31D | FULL MOON WITH FACE |  |
| 233 | 🌞 | U+1F31E | SUN WITH FACE |  |
| 234 | 🌟 | U+1F31F | GLOWING STAR |  |
| 235 | 🌠 | U+1F320 | SHOOTING STAR |  |
| 236 | 🌡 | U+1F321 | THERMOMETER |  |
| 237 | 🌤 | U+1F324 | WHITE SUN WITH SMALL CLOUD |  |
| 238 | 🌥 | U+1F325 | WHITE SUN BEHIND CLOUD |  |
| 239 | 🌦 | U+1F326 | WHITE SUN BEHIND CLOUD WITH RAIN |  |
| 240 | 🌧 | U+1F327 | CLOUD WITH RAIN |  |
| 241 | 🌨 | U+1F328 | CLOUD WITH SNOW |  |
| 242 | 🌩 | U+1F329 | CLOUD WITH LIGHTNING |  |
| 243 | 🌪 | U+1F32A | CLOUD WITH TORNADO |  |
| 244 | 🌫 | U+1F32B | FOG |  |
| 245 | 🌬 | U+1F32C | WIND BLOWING FACE |  |

<a id="sec-5"></a>
### 3.5 食物与饮料（U+1F32D–U+1F37F，83 个）

| # | 图标 | 码点 | Unicode 名称 | 卡片标记 |
|:---:|:---:|:---:|:---|:---|
| 246 | 🌭 | U+1F32D | HOT DOG |  |
| 247 | 🌮 | U+1F32E | TACO |  |
| 248 | 🌯 | U+1F32F | BURRITO |  |
| 249 | 🌰 | U+1F330 | CHESTNUT |  |
| 250 | 🌱 | U+1F331 | SEEDLING |  |
| 251 | 🌲 | U+1F332 | EVERGREEN TREE |  |
| 252 | 🌳 | U+1F333 | DECIDUOUS TREE |  |
| 253 | 🌴 | U+1F334 | PALM TREE |  |
| 254 | 🌵 | U+1F335 | CACTUS |  |
| 255 | 🌶 | U+1F336 | HOT PEPPER |  |
| 256 | 🌷 | U+1F337 | TULIP |  |
| 257 | 🌸 | U+1F338 | CHERRY BLOSSOM |  |
| 258 | 🌹 | U+1F339 | ROSE |  |
| 259 | 🌺 | U+1F33A | HIBISCUS |  |
| 260 | 🌻 | U+1F33B | SUNFLOWER |  |
| 261 | 🌼 | U+1F33C | BLOSSOM |  |
| 262 | 🌽 | U+1F33D | EAR OF MAIZE |  |
| 263 | 🌾 | U+1F33E | EAR OF RICE |  |
| 264 | 🌿 | U+1F33F | HERB |  |
| 265 | 🍀 | U+1F340 | FOUR LEAF CLOVER |  |
| 266 | 🍁 | U+1F341 | MAPLE LEAF |  |
| 267 | 🍂 | U+1F342 | FALLEN LEAF |  |
| 268 | 🍃 | U+1F343 | LEAF FLUTTERING IN WIND |  |
| 269 | 🍄 | U+1F344 | MUSHROOM |  |
| 270 | 🍅 | U+1F345 | TOMATO |  |
| 271 | 🍆 | U+1F346 | AUBERGINE |  |
| 272 | 🍇 | U+1F347 | GRAPES |  |
| 273 | 🍈 | U+1F348 | MELON |  |
| 274 | 🍉 | U+1F349 | WATERMELON |  |
| 275 | 🍊 | U+1F34A | TANGERINE |  |
| 276 | 🍋 | U+1F34B | LEMON |  |
| 277 | 🍌 | U+1F34C | BANANA |  |
| 278 | 🍍 | U+1F34D | PINEAPPLE |  |
| 279 | 🍎 | U+1F34E | RED APPLE |  |
| 280 | 🍏 | U+1F34F | GREEN APPLE |  |
| 281 | 🍐 | U+1F350 | PEAR |  |
| 282 | 🍑 | U+1F351 | PEACH |  |
| 283 | 🍒 | U+1F352 | CHERRIES |  |
| 284 | 🍓 | U+1F353 | STRAWBERRY |  |
| 285 | 🍔 | U+1F354 | HAMBURGER |  |
| 286 | 🍕 | U+1F355 | SLICE OF PIZZA |  |
| 287 | 🍖 | U+1F356 | MEAT ON BONE |  |
| 288 | 🍗 | U+1F357 | POULTRY LEG |  |
| 289 | 🍘 | U+1F358 | RICE CRACKER |  |
| 290 | 🍙 | U+1F359 | RICE BALL |  |
| 291 | 🍚 | U+1F35A | COOKED RICE |  |
| 292 | 🍛 | U+1F35B | CURRY AND RICE |  |
| 293 | 🍜 | U+1F35C | STEAMING BOWL |  |
| 294 | 🍝 | U+1F35D | SPAGHETTI |  |
| 295 | 🍞 | U+1F35E | BREAD |  |
| 296 | 🍟 | U+1F35F | FRENCH FRIES |  |
| 297 | 🍠 | U+1F360 | ROASTED SWEET POTATO |  |
| 298 | 🍡 | U+1F361 | DANGO |  |
| 299 | 🍢 | U+1F362 | ODEN |  |
| 300 | 🍣 | U+1F363 | SUSHI |  |
| 301 | 🍤 | U+1F364 | FRIED SHRIMP |  |
| 302 | 🍥 | U+1F365 | FISH CAKE WITH SWIRL DESIGN |  |
| 303 | 🍦 | U+1F366 | SOFT ICE CREAM |  |
| 304 | 🍧 | U+1F367 | SHAVED ICE |  |
| 305 | 🍨 | U+1F368 | ICE CREAM |  |
| 306 | 🍩 | U+1F369 | DOUGHNUT |  |
| 307 | 🍪 | U+1F36A | COOKIE |  |
| 308 | 🍫 | U+1F36B | CHOCOLATE BAR |  |
| 309 | 🍬 | U+1F36C | CANDY |  |
| 310 | 🍭 | U+1F36D | LOLLIPOP |  |
| 311 | 🍮 | U+1F36E | CUSTARD |  |
| 312 | 🍯 | U+1F36F | HONEY POT |  |
| 313 | 🍰 | U+1F370 | SHORTCAKE |  |
| 314 | 🍱 | U+1F371 | BENTO BOX |  |
| 315 | 🍲 | U+1F372 | POT OF FOOD |  |
| 316 | 🍳 | U+1F373 | COOKING |  |
| 317 | 🍴 | U+1F374 | FORK AND KNIFE |  |
| 318 | 🍵 | U+1F375 | TEACUP WITHOUT HANDLE |  |
| 319 | 🍶 | U+1F376 | SAKE BOTTLE AND CUP |  |
| 320 | 🍷 | U+1F377 | WINE GLASS |  |
| 321 | 🍸 | U+1F378 | COCKTAIL GLASS |  |
| 322 | 🍹 | U+1F379 | TROPICAL DRINK |  |
| 323 | 🍺 | U+1F37A | BEER MUG |  |
| 324 | 🍻 | U+1F37B | CLINKING BEER MUGS |  |
| 325 | 🍼 | U+1F37C | BABY BOTTLE |  |
| 326 | 🍽 | U+1F37D | FORK AND KNIFE WITH PLATE |  |
| 327 | 🍾 | U+1F37E | BOTTLE WITH POPPING CORK |  |
| 328 | 🍿 | U+1F37F | POPCORN |  |

<a id="sec-6"></a>
### 3.6 活动、运动与地点（U+1F380–U+1F3FF，115 个）

| # | 图标 | 码点 | Unicode 名称 | 卡片标记 |
|:---:|:---:|:---:|:---|:---|
| 329 | 🎀 | U+1F380 | RIBBON |  |
| 330 | 🎁 | U+1F381 | WRAPPED PRESENT |  |
| 331 | 🎂 | U+1F382 | BIRTHDAY CAKE |  |
| 332 | 🎃 | U+1F383 | JACK-O-LANTERN |  |
| 333 | 🎄 | U+1F384 | CHRISTMAS TREE |  |
| 334 | 🎅 | U+1F385 | FATHER CHRISTMAS |  |
| 335 | 🎆 | U+1F386 | FIREWORKS |  |
| 336 | 🎇 | U+1F387 | FIREWORK SPARKLER |  |
| 337 | 🎈 | U+1F388 | BALLOON |  |
| 338 | 🎉 | U+1F389 | PARTY POPPER |  |
| 339 | 🎊 | U+1F38A | CONFETTI BALL |  |
| 340 | 🎋 | U+1F38B | TANABATA TREE |  |
| 341 | 🎌 | U+1F38C | CROSSED FLAGS |  |
| 342 | 🎍 | U+1F38D | PINE DECORATION |  |
| 343 | 🎎 | U+1F38E | JAPANESE DOLLS |  |
| 344 | 🎏 | U+1F38F | CARP STREAMER |  |
| 345 | 🎐 | U+1F390 | WIND CHIME |  |
| 346 | 🎑 | U+1F391 | MOON VIEWING CEREMONY |  |
| 347 | 🎒 | U+1F392 | SCHOOL SATCHEL |  |
| 348 | 🎓 | U+1F393 | GRADUATION CAP |  |
| 349 | 🎖 | U+1F396 | MILITARY MEDAL |  |
| 350 | 🎗 | U+1F397 | REMINDER RIBBON |  |
| 351 | 🎙 | U+1F399 | STUDIO MICROPHONE |  |
| 352 | 🎚 | U+1F39A | LEVEL SLIDER |  |
| 353 | 🎛 | U+1F39B | CONTROL KNOBS |  |
| 354 | 🎞 | U+1F39E | FILM FRAMES |  |
| 355 | 🎟 | U+1F39F | ADMISSION TICKETS |  |
| 356 | 🎠 | U+1F3A0 | CAROUSEL HORSE |  |
| 357 | 🎡 | U+1F3A1 | FERRIS WHEEL |  |
| 358 | 🎢 | U+1F3A2 | ROLLER COASTER |  |
| 359 | 🎣 | U+1F3A3 | FISHING POLE AND FISH |  |
| 360 | 🎤 | U+1F3A4 | MICROPHONE |  |
| 361 | 🎥 | U+1F3A5 | MOVIE CAMERA |  |
| 362 | 🎦 | U+1F3A6 | CINEMA |  |
| 363 | 🎧 | U+1F3A7 | HEADPHONE |  |
| 364 | 🎨 | U+1F3A8 | ARTIST PALETTE |  |
| 365 | 🎩 | U+1F3A9 | TOP HAT |  |
| 366 | 🎪 | U+1F3AA | CIRCUS TENT |  |
| 367 | 🎫 | U+1F3AB | TICKET |  |
| 368 | 🎬 | U+1F3AC | CLAPPER BOARD |  |
| 369 | 🎭 | U+1F3AD | PERFORMING ARTS |  |
| 370 | 🎮 | U+1F3AE | VIDEO GAME |  |
| 371 | 🎯 | U+1F3AF | DIRECT HIT | 建议 · todo_write |
| 372 | 🎰 | U+1F3B0 | SLOT MACHINE |  |
| 373 | 🎱 | U+1F3B1 | BILLIARDS |  |
| 374 | 🎲 | U+1F3B2 | GAME DIE |  |
| 375 | 🎳 | U+1F3B3 | BOWLING |  |
| 376 | 🎴 | U+1F3B4 | FLOWER PLAYING CARDS |  |
| 377 | 🎵 | U+1F3B5 | MUSICAL NOTE |  |
| 378 | 🎶 | U+1F3B6 | MULTIPLE MUSICAL NOTES |  |
| 379 | 🎷 | U+1F3B7 | SAXOPHONE |  |
| 380 | 🎸 | U+1F3B8 | GUITAR |  |
| 381 | 🎹 | U+1F3B9 | MUSICAL KEYBOARD |  |
| 382 | 🎺 | U+1F3BA | TRUMPET |  |
| 383 | 🎻 | U+1F3BB | VIOLIN |  |
| 384 | 🎼 | U+1F3BC | MUSICAL SCORE |  |
| 385 | 🎽 | U+1F3BD | RUNNING SHIRT WITH SASH |  |
| 386 | 🎾 | U+1F3BE | TENNIS RACQUET AND BALL |  |
| 387 | 🎿 | U+1F3BF | SKI AND SKI BOOT |  |
| 388 | 🏀 | U+1F3C0 | BASKETBALL AND HOOP |  |
| 389 | 🏁 | U+1F3C1 | CHEQUERED FLAG |  |
| 390 | 🏂 | U+1F3C2 | SNOWBOARDER |  |
| 391 | 🏃 | U+1F3C3 | RUNNER |  |
| 392 | 🏄 | U+1F3C4 | SURFER |  |
| 393 | 🏅 | U+1F3C5 | SPORTS MEDAL |  |
| 394 | 🏆 | U+1F3C6 | TROPHY |  |
| 395 | 🏇 | U+1F3C7 | HORSE RACING |  |
| 396 | 🏈 | U+1F3C8 | AMERICAN FOOTBALL |  |
| 397 | 🏉 | U+1F3C9 | RUGBY FOOTBALL |  |
| 398 | 🏊 | U+1F3CA | SWIMMER |  |
| 399 | 🏋 | U+1F3CB | WEIGHT LIFTER |  |
| 400 | 🏌 | U+1F3CC | GOLFER |  |
| 401 | 🏍 | U+1F3CD | RACING MOTORCYCLE |  |
| 402 | 🏎 | U+1F3CE | RACING CAR |  |
| 403 | 🏏 | U+1F3CF | CRICKET BAT AND BALL |  |
| 404 | 🏐 | U+1F3D0 | VOLLEYBALL |  |
| 405 | 🏑 | U+1F3D1 | FIELD HOCKEY STICK AND BALL |  |
| 406 | 🏒 | U+1F3D2 | ICE HOCKEY STICK AND PUCK |  |
| 407 | 🏓 | U+1F3D3 | TABLE TENNIS PADDLE AND BALL |  |
| 408 | 🏔 | U+1F3D4 | SNOW CAPPED MOUNTAIN |  |
| 409 | 🏕 | U+1F3D5 | CAMPING |  |
| 410 | 🏖 | U+1F3D6 | BEACH WITH UMBRELLA |  |
| 411 | 🏗 | U+1F3D7 | BUILDING CONSTRUCTION |  |
| 412 | 🏘 | U+1F3D8 | HOUSE BUILDINGS |  |
| 413 | 🏙 | U+1F3D9 | CITYSCAPE |  |
| 414 | 🏚 | U+1F3DA | DERELICT HOUSE BUILDING |  |
| 415 | 🏛 | U+1F3DB | CLASSICAL BUILDING |  |
| 416 | 🏜 | U+1F3DC | DESERT |  |
| 417 | 🏝 | U+1F3DD | DESERT ISLAND |  |
| 418 | 🏞 | U+1F3DE | NATIONAL PARK |  |
| 419 | 🏟 | U+1F3DF | STADIUM |  |
| 420 | 🏠 | U+1F3E0 | HOUSE BUILDING |  |
| 421 | 🏡 | U+1F3E1 | HOUSE WITH GARDEN |  |
| 422 | 🏢 | U+1F3E2 | OFFICE BUILDING |  |
| 423 | 🏣 | U+1F3E3 | JAPANESE POST OFFICE |  |
| 424 | 🏤 | U+1F3E4 | EUROPEAN POST OFFICE |  |
| 425 | 🏥 | U+1F3E5 | HOSPITAL |  |
| 426 | 🏦 | U+1F3E6 | BANK |  |
| 427 | 🏧 | U+1F3E7 | AUTOMATED TELLER MACHINE |  |
| 428 | 🏨 | U+1F3E8 | HOTEL |  |
| 429 | 🏩 | U+1F3E9 | LOVE HOTEL |  |
| 430 | 🏪 | U+1F3EA | CONVENIENCE STORE |  |
| 431 | 🏫 | U+1F3EB | SCHOOL |  |
| 432 | 🏬 | U+1F3EC | DEPARTMENT STORE |  |
| 433 | 🏭 | U+1F3ED | FACTORY |  |
| 434 | 🏮 | U+1F3EE | IZAKAYA LANTERN |  |
| 435 | 🏯 | U+1F3EF | JAPANESE CASTLE |  |
| 436 | 🏰 | U+1F3F0 | EUROPEAN CASTLE |  |
| 437 | 🏳 | U+1F3F3 | WAVING WHITE FLAG |  |
| 438 | 🏴 | U+1F3F4 | WAVING BLACK FLAG |  |
| 439 | 🏵 | U+1F3F5 | ROSETTE |  |
| 440 | 🏷 | U+1F3F7 | LABEL |  |
| 441 | 🏸 | U+1F3F8 | BADMINTON RACQUET AND SHUTTLECOCK |  |
| 442 | 🏹 | U+1F3F9 | BOW AND ARROW |  |
| 443 | 🏺 | U+1F3FA | AMPHORA |  |

<a id="sec-7"></a>
### 3.7 动物与自然（U+1F400–U+1F43F，64 个）

| # | 图标 | 码点 | Unicode 名称 | 卡片标记 |
|:---:|:---:|:---:|:---|:---|
| 444 | 🐀 | U+1F400 | RAT |  |
| 445 | 🐁 | U+1F401 | MOUSE |  |
| 446 | 🐂 | U+1F402 | OX |  |
| 447 | 🐃 | U+1F403 | WATER BUFFALO |  |
| 448 | 🐄 | U+1F404 | COW |  |
| 449 | 🐅 | U+1F405 | TIGER |  |
| 450 | 🐆 | U+1F406 | LEOPARD |  |
| 451 | 🐇 | U+1F407 | RABBIT |  |
| 452 | 🐈 | U+1F408 | CAT |  |
| 453 | 🐉 | U+1F409 | DRAGON |  |
| 454 | 🐊 | U+1F40A | CROCODILE |  |
| 455 | 🐋 | U+1F40B | WHALE |  |
| 456 | 🐌 | U+1F40C | SNAIL |  |
| 457 | 🐍 | U+1F40D | SNAKE |  |
| 458 | 🐎 | U+1F40E | HORSE |  |
| 459 | 🐏 | U+1F40F | RAM |  |
| 460 | 🐐 | U+1F410 | GOAT |  |
| 461 | 🐑 | U+1F411 | SHEEP |  |
| 462 | 🐒 | U+1F412 | MONKEY |  |
| 463 | 🐓 | U+1F413 | ROOSTER |  |
| 464 | 🐔 | U+1F414 | CHICKEN |  |
| 465 | 🐕 | U+1F415 | DOG |  |
| 466 | 🐖 | U+1F416 | PIG |  |
| 467 | 🐗 | U+1F417 | BOAR |  |
| 468 | 🐘 | U+1F418 | ELEPHANT |  |
| 469 | 🐙 | U+1F419 | OCTOPUS |  |
| 470 | 🐚 | U+1F41A | SPIRAL SHELL |  |
| 471 | 🐛 | U+1F41B | BUG |  |
| 472 | 🐜 | U+1F41C | ANT |  |
| 473 | 🐝 | U+1F41D | HONEYBEE |  |
| 474 | 🐞 | U+1F41E | LADY BEETLE |  |
| 475 | 🐟 | U+1F41F | FISH |  |
| 476 | 🐠 | U+1F420 | TROPICAL FISH |  |
| 477 | 🐡 | U+1F421 | BLOWFISH |  |
| 478 | 🐢 | U+1F422 | TURTLE |  |
| 479 | 🐣 | U+1F423 | HATCHING CHICK |  |
| 480 | 🐤 | U+1F424 | BABY CHICK |  |
| 481 | 🐥 | U+1F425 | FRONT-FACING BABY CHICK |  |
| 482 | 🐦 | U+1F426 | BIRD |  |
| 483 | 🐧 | U+1F427 | PENGUIN |  |
| 484 | 🐨 | U+1F428 | KOALA |  |
| 485 | 🐩 | U+1F429 | POODLE |  |
| 486 | 🐪 | U+1F42A | DROMEDARY CAMEL |  |
| 487 | 🐫 | U+1F42B | BACTRIAN CAMEL |  |
| 488 | 🐬 | U+1F42C | DOLPHIN |  |
| 489 | 🐭 | U+1F42D | MOUSE FACE |  |
| 490 | 🐮 | U+1F42E | COW FACE |  |
| 491 | 🐯 | U+1F42F | TIGER FACE |  |
| 492 | 🐰 | U+1F430 | RABBIT FACE |  |
| 493 | 🐱 | U+1F431 | CAT FACE |  |
| 494 | 🐲 | U+1F432 | DRAGON FACE |  |
| 495 | 🐳 | U+1F433 | SPOUTING WHALE |  |
| 496 | 🐴 | U+1F434 | HORSE FACE |  |
| 497 | 🐵 | U+1F435 | MONKEY FACE |  |
| 498 | 🐶 | U+1F436 | DOG FACE |  |
| 499 | 🐷 | U+1F437 | PIG FACE |  |
| 500 | 🐸 | U+1F438 | FROG FACE |  |
| 501 | 🐹 | U+1F439 | HAMSTER FACE |  |
| 502 | 🐺 | U+1F43A | WOLF FACE |  |
| 503 | 🐻 | U+1F43B | BEAR FACE |  |
| 504 | 🐼 | U+1F43C | PANDA FACE |  |
| 505 | 🐽 | U+1F43D | PIG NOSE |  |
| 506 | 🐾 | U+1F43E | PAW PRINTS |  |
| 507 | 🐿 | U+1F43F | CHIPMUNK |  |

<a id="sec-8"></a>
### 3.8 表情、人物与手势（U+1F440–U+1F49F，96 个）

| # | 图标 | 码点 | Unicode 名称 | 卡片标记 |
|:---:|:---:|:---:|:---|:---|
| 508 | 👀 | U+1F440 | EYES | 备选 · read_image |
| 509 | 👁 | U+1F441 | EYE | 建议 · read_image（显示用 U+1F441 U+FE0F） |
| 510 | 👂 | U+1F442 | EAR |  |
| 511 | 👃 | U+1F443 | NOSE |  |
| 512 | 👄 | U+1F444 | MOUTH |  |
| 513 | 👅 | U+1F445 | TONGUE |  |
| 514 | 👆 | U+1F446 | WHITE UP POINTING BACKHAND INDEX |  |
| 515 | 👇 | U+1F447 | WHITE DOWN POINTING BACKHAND INDEX |  |
| 516 | 👈 | U+1F448 | WHITE LEFT POINTING BACKHAND INDEX |  |
| 517 | 👉 | U+1F449 | WHITE RIGHT POINTING BACKHAND INDEX |  |
| 518 | 👊 | U+1F44A | FISTED HAND SIGN |  |
| 519 | 👋 | U+1F44B | WAVING HAND SIGN |  |
| 520 | 👌 | U+1F44C | OK HAND SIGN |  |
| 521 | 👍 | U+1F44D | THUMBS UP SIGN |  |
| 522 | 👎 | U+1F44E | THUMBS DOWN SIGN |  |
| 523 | 👏 | U+1F44F | CLAPPING HANDS SIGN |  |
| 524 | 👐 | U+1F450 | OPEN HANDS SIGN |  |
| 525 | 👑 | U+1F451 | CROWN |  |
| 526 | 👒 | U+1F452 | WOMANS HAT |  |
| 527 | 👓 | U+1F453 | EYEGLASSES |  |
| 528 | 👔 | U+1F454 | NECKTIE |  |
| 529 | 👕 | U+1F455 | T-SHIRT |  |
| 530 | 👖 | U+1F456 | JEANS |  |
| 531 | 👗 | U+1F457 | DRESS |  |
| 532 | 👘 | U+1F458 | KIMONO |  |
| 533 | 👙 | U+1F459 | BIKINI |  |
| 534 | 👚 | U+1F45A | WOMANS CLOTHES |  |
| 535 | 👛 | U+1F45B | PURSE |  |
| 536 | 👜 | U+1F45C | HANDBAG |  |
| 537 | 👝 | U+1F45D | POUCH |  |
| 538 | 👞 | U+1F45E | MANS SHOE |  |
| 539 | 👟 | U+1F45F | ATHLETIC SHOE |  |
| 540 | 👠 | U+1F460 | HIGH-HEELED SHOE |  |
| 541 | 👡 | U+1F461 | WOMANS SANDAL |  |
| 542 | 👢 | U+1F462 | WOMANS BOOTS |  |
| 543 | 👣 | U+1F463 | FOOTPRINTS |  |
| 544 | 👤 | U+1F464 | BUST IN SILHOUETTE |  |
| 545 | 👥 | U+1F465 | BUSTS IN SILHOUETTE |  |
| 546 | 👦 | U+1F466 | BOY |  |
| 547 | 👧 | U+1F467 | GIRL |  |
| 548 | 👨 | U+1F468 | MAN |  |
| 549 | 👩 | U+1F469 | WOMAN |  |
| 550 | 👪 | U+1F46A | FAMILY |  |
| 551 | 👫 | U+1F46B | MAN AND WOMAN HOLDING HANDS |  |
| 552 | 👬 | U+1F46C | TWO MEN HOLDING HANDS |  |
| 553 | 👭 | U+1F46D | TWO WOMEN HOLDING HANDS |  |
| 554 | 👮 | U+1F46E | POLICE OFFICER |  |
| 555 | 👯 | U+1F46F | WOMAN WITH BUNNY EARS |  |
| 556 | 👰 | U+1F470 | BRIDE WITH VEIL |  |
| 557 | 👱 | U+1F471 | PERSON WITH BLOND HAIR |  |
| 558 | 👲 | U+1F472 | MAN WITH GUA PI MAO |  |
| 559 | 👳 | U+1F473 | MAN WITH TURBAN |  |
| 560 | 👴 | U+1F474 | OLDER MAN |  |
| 561 | 👵 | U+1F475 | OLDER WOMAN |  |
| 562 | 👶 | U+1F476 | BABY |  |
| 563 | 👷 | U+1F477 | CONSTRUCTION WORKER |  |
| 564 | 👸 | U+1F478 | PRINCESS |  |
| 565 | 👹 | U+1F479 | JAPANESE OGRE |  |
| 566 | 👺 | U+1F47A | JAPANESE GOBLIN |  |
| 567 | 👻 | U+1F47B | GHOST |  |
| 568 | 👼 | U+1F47C | BABY ANGEL |  |
| 569 | 👽 | U+1F47D | EXTRATERRESTRIAL ALIEN |  |
| 570 | 👾 | U+1F47E | ALIEN MONSTER |  |
| 571 | 👿 | U+1F47F | IMP |  |
| 572 | 💀 | U+1F480 | SKULL |  |
| 573 | 💁 | U+1F481 | INFORMATION DESK PERSON |  |
| 574 | 💂 | U+1F482 | GUARDSMAN |  |
| 575 | 💃 | U+1F483 | DANCER |  |
| 576 | 💄 | U+1F484 | LIPSTICK |  |
| 577 | 💅 | U+1F485 | NAIL POLISH |  |
| 578 | 💆 | U+1F486 | FACE MASSAGE |  |
| 579 | 💇 | U+1F487 | HAIRCUT |  |
| 580 | 💈 | U+1F488 | BARBER POLE |  |
| 581 | 💉 | U+1F489 | SYRINGE |  |
| 582 | 💊 | U+1F48A | PILL |  |
| 583 | 💋 | U+1F48B | KISS MARK |  |
| 584 | 💌 | U+1F48C | LOVE LETTER |  |
| 585 | 💍 | U+1F48D | RING |  |
| 586 | 💎 | U+1F48E | GEM STONE |  |
| 587 | 💏 | U+1F48F | KISS |  |
| 588 | 💐 | U+1F490 | BOUQUET |  |
| 589 | 💑 | U+1F491 | COUPLE WITH HEART |  |
| 590 | 💒 | U+1F492 | WEDDING |  |
| 591 | 💓 | U+1F493 | BEATING HEART |  |
| 592 | 💔 | U+1F494 | BROKEN HEART |  |
| 593 | 💕 | U+1F495 | TWO HEARTS |  |
| 594 | 💖 | U+1F496 | SPARKLING HEART |  |
| 595 | 💗 | U+1F497 | GROWING HEART |  |
| 596 | 💘 | U+1F498 | HEART WITH ARROW |  |
| 597 | 💙 | U+1F499 | BLUE HEART |  |
| 598 | 💚 | U+1F49A | GREEN HEART |  |
| 599 | 💛 | U+1F49B | YELLOW HEART |  |
| 600 | 💜 | U+1F49C | PURPLE HEART |  |
| 601 | 💝 | U+1F49D | HEART WITH RIBBON |  |
| 602 | 💞 | U+1F49E | REVOLVING HEARTS |  |
| 603 | 💟 | U+1F49F | HEART DECORATION |  |

<a id="sec-9"></a>
### 3.9 物件与办公（U+1F4A0–U+1F4FF，95 个）

| # | 图标 | 码点 | Unicode 名称 | 卡片标记 |
|:---:|:---:|:---:|:---|:---|
| 604 | 💠 | U+1F4A0 | DIAMOND SHAPE WITH A DOT INSIDE |  |
| 605 | 💡 | U+1F4A1 | ELECTRIC LIGHT BULB |  |
| 606 | 💢 | U+1F4A2 | ANGER SYMBOL |  |
| 607 | 💣 | U+1F4A3 | BOMB |  |
| 608 | 💤 | U+1F4A4 | SLEEPING SYMBOL |  |
| 609 | 💥 | U+1F4A5 | COLLISION SYMBOL |  |
| 610 | 💦 | U+1F4A6 | SPLASHING SWEAT SYMBOL |  |
| 611 | 💧 | U+1F4A7 | DROPLET |  |
| 612 | 💨 | U+1F4A8 | DASH SYMBOL |  |
| 613 | 💩 | U+1F4A9 | PILE OF POO |  |
| 614 | 💪 | U+1F4AA | FLEXED BICEPS |  |
| 615 | 💫 | U+1F4AB | DIZZY SYMBOL |  |
| 616 | 💬 | U+1F4AC | SPEECH BALLOON |  |
| 617 | 💭 | U+1F4AD | THOUGHT BALLOON |  |
| 618 | 💮 | U+1F4AE | WHITE FLOWER |  |
| 619 | 💯 | U+1F4AF | HUNDRED POINTS SYMBOL |  |
| 620 | 💰 | U+1F4B0 | MONEY BAG |  |
| 621 | 💱 | U+1F4B1 | CURRENCY EXCHANGE |  |
| 622 | 💲 | U+1F4B2 | HEAVY DOLLAR SIGN |  |
| 623 | 💳 | U+1F4B3 | CREDIT CARD |  |
| 624 | 💴 | U+1F4B4 | BANKNOTE WITH YEN SIGN |  |
| 625 | 💵 | U+1F4B5 | BANKNOTE WITH DOLLAR SIGN |  |
| 626 | 💶 | U+1F4B6 | BANKNOTE WITH EURO SIGN |  |
| 627 | 💷 | U+1F4B7 | BANKNOTE WITH POUND SIGN |  |
| 628 | 💸 | U+1F4B8 | MONEY WITH WINGS |  |
| 629 | 💹 | U+1F4B9 | CHART WITH UPWARDS TREND AND YEN SIGN |  |
| 630 | 💺 | U+1F4BA | SEAT |  |
| 631 | 💻 | U+1F4BB | PERSONAL COMPUTER | 现行 · execute |
| 632 | 💼 | U+1F4BC | BRIEFCASE |  |
| 633 | 💽 | U+1F4BD | MINIDISC |  |
| 634 | 💾 | U+1F4BE | FLOPPY DISK |  |
| 635 | 💿 | U+1F4BF | OPTICAL DISC |  |
| 636 | 📀 | U+1F4C0 | DVD |  |
| 637 | 📁 | U+1F4C1 | FILE FOLDER |  |
| 638 | 📂 | U+1F4C2 | OPEN FILE FOLDER |  |
| 639 | 📃 | U+1F4C3 | PAGE WITH CURL |  |
| 640 | 📄 | U+1F4C4 | PAGE FACING UP | 现行 · read |
| 641 | 📅 | U+1F4C5 | CALENDAR |  |
| 642 | 📆 | U+1F4C6 | TEAR-OFF CALENDAR |  |
| 643 | 📇 | U+1F4C7 | CARD INDEX |  |
| 644 | 📈 | U+1F4C8 | CHART WITH UPWARDS TREND |  |
| 645 | 📉 | U+1F4C9 | CHART WITH DOWNWARDS TREND |  |
| 646 | 📊 | U+1F4CA | BAR CHART |  |
| 647 | 📋 | U+1F4CB | CLIPBOARD | 现行 · TodoCard |
| 648 | 📌 | U+1F4CC | PUSHPIN | 备选 · TodoCard |
| 649 | 📍 | U+1F4CD | ROUND PUSHPIN |  |
| 650 | 📎 | U+1F4CE | PAPERCLIP |  |
| 651 | 📏 | U+1F4CF | STRAIGHT RULER |  |
| 652 | 📐 | U+1F4D0 | TRIANGULAR RULER |  |
| 653 | 📑 | U+1F4D1 | BOOKMARK TABS | 备选 · TodoCard |
| 654 | 📒 | U+1F4D2 | LEDGER |  |
| 655 | 📓 | U+1F4D3 | NOTEBOOK |  |
| 656 | 📔 | U+1F4D4 | NOTEBOOK WITH DECORATIVE COVER |  |
| 657 | 📕 | U+1F4D5 | CLOSED BOOK |  |
| 658 | 📖 | U+1F4D6 | OPEN BOOK |  |
| 659 | 📗 | U+1F4D7 | GREEN BOOK |  |
| 660 | 📘 | U+1F4D8 | BLUE BOOK |  |
| 661 | 📙 | U+1F4D9 | ORANGE BOOK |  |
| 662 | 📚 | U+1F4DA | BOOKS |  |
| 663 | 📛 | U+1F4DB | NAME BADGE |  |
| 664 | 📜 | U+1F4DC | SCROLL |  |
| 665 | 📝 | U+1F4DD | MEMO | 现行 · edit |
| 666 | 📞 | U+1F4DE | TELEPHONE RECEIVER |  |
| 667 | 📟 | U+1F4DF | PAGER |  |
| 668 | 📠 | U+1F4E0 | FAX MACHINE |  |
| 669 | 📡 | U+1F4E1 | SATELLITE ANTENNA |  |
| 670 | 📢 | U+1F4E2 | PUBLIC ADDRESS LOUDSPEAKER |  |
| 671 | 📣 | U+1F4E3 | CHEERING MEGAPHONE |  |
| 672 | 📤 | U+1F4E4 | OUTBOX TRAY |  |
| 673 | 📥 | U+1F4E5 | INBOX TRAY |  |
| 674 | 📦 | U+1F4E6 | PACKAGE |  |
| 675 | 📧 | U+1F4E7 | E-MAIL SYMBOL |  |
| 676 | 📨 | U+1F4E8 | INCOMING ENVELOPE |  |
| 677 | 📩 | U+1F4E9 | ENVELOPE WITH DOWNWARDS ARROW ABOVE |  |
| 678 | 📪 | U+1F4EA | CLOSED MAILBOX WITH LOWERED FLAG |  |
| 679 | 📫 | U+1F4EB | CLOSED MAILBOX WITH RAISED FLAG |  |
| 680 | 📬 | U+1F4EC | OPEN MAILBOX WITH RAISED FLAG |  |
| 681 | 📭 | U+1F4ED | OPEN MAILBOX WITH LOWERED FLAG |  |
| 682 | 📮 | U+1F4EE | POSTBOX |  |
| 683 | 📯 | U+1F4EF | POSTAL HORN |  |
| 684 | 📰 | U+1F4F0 | NEWSPAPER |  |
| 685 | 📱 | U+1F4F1 | MOBILE PHONE |  |
| 686 | 📲 | U+1F4F2 | MOBILE PHONE WITH RIGHTWARDS ARROW AT LEFT |  |
| 687 | 📳 | U+1F4F3 | VIBRATION MODE |  |
| 688 | 📴 | U+1F4F4 | MOBILE PHONE OFF |  |
| 689 | 📵 | U+1F4F5 | NO MOBILE PHONES |  |
| 690 | 📶 | U+1F4F6 | ANTENNA WITH BARS |  |
| 691 | 📷 | U+1F4F7 | CAMERA | 备选 · read_image |
| 692 | 📸 | U+1F4F8 | CAMERA WITH FLASH | 备选 · read_image |
| 693 | 📹 | U+1F4F9 | VIDEO CAMERA |  |
| 694 | 📺 | U+1F4FA | TELEVISION |  |
| 695 | 📻 | U+1F4FB | RADIO |  |
| 696 | 📼 | U+1F4FC | VIDEOCASSETTE |  |
| 697 | 📽 | U+1F4FD | FILM PROJECTOR |  |
| 698 | 📿 | U+1F4FF | PRAYER BEADS |  |

<a id="sec-10"></a>
### 3.10 物件与生活（U+1F500–U+1F5FF，136 个）

| # | 图标 | 码点 | Unicode 名称 | 卡片标记 |
|:---:|:---:|:---:|:---|:---|
| 699 | 🔀 | U+1F500 | TWISTED RIGHTWARDS ARROWS |  |
| 700 | 🔁 | U+1F501 | CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS |  |
| 701 | 🔂 | U+1F502 | CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS WITH CIRCLED ONE OVERLAY |  |
| 702 | 🔃 | U+1F503 | CLOCKWISE DOWNWARDS AND UPWARDS OPEN CIRCLE ARROWS |  |
| 703 | 🔄 | U+1F504 | ANTICLOCKWISE DOWNWARDS AND UPWARDS OPEN CIRCLE ARROWS |  |
| 704 | 🔅 | U+1F505 | LOW BRIGHTNESS SYMBOL |  |
| 705 | 🔆 | U+1F506 | HIGH BRIGHTNESS SYMBOL |  |
| 706 | 🔇 | U+1F507 | SPEAKER WITH CANCELLATION STROKE |  |
| 707 | 🔈 | U+1F508 | SPEAKER |  |
| 708 | 🔉 | U+1F509 | SPEAKER WITH ONE SOUND WAVE |  |
| 709 | 🔊 | U+1F50A | SPEAKER WITH THREE SOUND WAVES |  |
| 710 | 🔋 | U+1F50B | BATTERY |  |
| 711 | 🔌 | U+1F50C | ELECTRIC PLUG | 建议 · MCP 服务器（仅 mcp__ 前缀） |
| 712 | 🔍 | U+1F50D | LEFT-POINTING MAGNIFYING GLASS | 现行 · search |
| 713 | 🔎 | U+1F50E | RIGHT-POINTING MAGNIFYING GLASS |  |
| 714 | 🔏 | U+1F50F | LOCK WITH INK PEN |  |
| 715 | 🔐 | U+1F510 | CLOSED LOCK WITH KEY |  |
| 716 | 🔑 | U+1F511 | KEY |  |
| 717 | 🔒 | U+1F512 | LOCK |  |
| 718 | 🔓 | U+1F513 | OPEN LOCK |  |
| 719 | 🔔 | U+1F514 | BELL |  |
| 720 | 🔕 | U+1F515 | BELL WITH CANCELLATION STROKE |  |
| 721 | 🔖 | U+1F516 | BOOKMARK |  |
| 722 | 🔗 | U+1F517 | LINK SYMBOL |  |
| 723 | 🔘 | U+1F518 | RADIO BUTTON |  |
| 724 | 🔙 | U+1F519 | BACK WITH LEFTWARDS ARROW ABOVE |  |
| 725 | 🔚 | U+1F51A | END WITH LEFTWARDS ARROW ABOVE |  |
| 726 | 🔛 | U+1F51B | ON WITH EXCLAMATION MARK WITH LEFT RIGHT ARROW ABOVE |  |
| 727 | 🔜 | U+1F51C | SOON WITH RIGHTWARDS ARROW ABOVE |  |
| 728 | 🔝 | U+1F51D | TOP WITH UPWARDS ARROW ABOVE |  |
| 729 | 🔞 | U+1F51E | NO ONE UNDER EIGHTEEN SYMBOL |  |
| 730 | 🔟 | U+1F51F | KEYCAP TEN |  |
| 731 | 🔠 | U+1F520 | INPUT SYMBOL FOR LATIN CAPITAL LETTERS |  |
| 732 | 🔡 | U+1F521 | INPUT SYMBOL FOR LATIN SMALL LETTERS |  |
| 733 | 🔢 | U+1F522 | INPUT SYMBOL FOR NUMBERS |  |
| 734 | 🔣 | U+1F523 | INPUT SYMBOL FOR SYMBOLS |  |
| 735 | 🔤 | U+1F524 | INPUT SYMBOL FOR LATIN LETTERS |  |
| 736 | 🔥 | U+1F525 | FIRE |  |
| 737 | 🔦 | U+1F526 | ELECTRIC TORCH |  |
| 738 | 🔧 | U+1F527 | WRENCH |  |
| 739 | 🔨 | U+1F528 | HAMMER |  |
| 740 | 🔩 | U+1F529 | NUT AND BOLT |  |
| 741 | 🔪 | U+1F52A | HOCHO |  |
| 742 | 🔫 | U+1F52B | PISTOL |  |
| 743 | 🔬 | U+1F52C | MICROSCOPE |  |
| 744 | 🔭 | U+1F52D | TELESCOPE |  |
| 745 | 🔮 | U+1F52E | CRYSTAL BALL |  |
| 746 | 🔯 | U+1F52F | SIX POINTED STAR WITH MIDDLE DOT |  |
| 747 | 🔰 | U+1F530 | JAPANESE SYMBOL FOR BEGINNER |  |
| 748 | 🔱 | U+1F531 | TRIDENT EMBLEM |  |
| 749 | 🔲 | U+1F532 | BLACK SQUARE BUTTON |  |
| 750 | 🔳 | U+1F533 | WHITE SQUARE BUTTON |  |
| 751 | 🔴 | U+1F534 | LARGE RED CIRCLE |  |
| 752 | 🔵 | U+1F535 | LARGE BLUE CIRCLE |  |
| 753 | 🔶 | U+1F536 | LARGE ORANGE DIAMOND |  |
| 754 | 🔷 | U+1F537 | LARGE BLUE DIAMOND |  |
| 755 | 🔸 | U+1F538 | SMALL ORANGE DIAMOND |  |
| 756 | 🔹 | U+1F539 | SMALL BLUE DIAMOND |  |
| 757 | 🔺 | U+1F53A | UP-POINTING RED TRIANGLE |  |
| 758 | 🔻 | U+1F53B | DOWN-POINTING RED TRIANGLE |  |
| 759 | 🔼 | U+1F53C | UP-POINTING SMALL RED TRIANGLE |  |
| 760 | 🔽 | U+1F53D | DOWN-POINTING SMALL RED TRIANGLE |  |
| 761 | 🕉 | U+1F549 | OM SYMBOL |  |
| 762 | 🕊 | U+1F54A | DOVE OF PEACE |  |
| 763 | 🕋 | U+1F54B | KAABA |  |
| 764 | 🕌 | U+1F54C | MOSQUE |  |
| 765 | 🕍 | U+1F54D | SYNAGOGUE |  |
| 766 | 🕎 | U+1F54E | MENORAH WITH NINE BRANCHES |  |
| 767 | 🕐 | U+1F550 | CLOCK FACE ONE OCLOCK |  |
| 768 | 🕑 | U+1F551 | CLOCK FACE TWO OCLOCK |  |
| 769 | 🕒 | U+1F552 | CLOCK FACE THREE OCLOCK |  |
| 770 | 🕓 | U+1F553 | CLOCK FACE FOUR OCLOCK |  |
| 771 | 🕔 | U+1F554 | CLOCK FACE FIVE OCLOCK |  |
| 772 | 🕕 | U+1F555 | CLOCK FACE SIX OCLOCK |  |
| 773 | 🕖 | U+1F556 | CLOCK FACE SEVEN OCLOCK |  |
| 774 | 🕗 | U+1F557 | CLOCK FACE EIGHT OCLOCK |  |
| 775 | 🕘 | U+1F558 | CLOCK FACE NINE OCLOCK |  |
| 776 | 🕙 | U+1F559 | CLOCK FACE TEN OCLOCK |  |
| 777 | 🕚 | U+1F55A | CLOCK FACE ELEVEN OCLOCK |  |
| 778 | 🕛 | U+1F55B | CLOCK FACE TWELVE OCLOCK |  |
| 779 | 🕜 | U+1F55C | CLOCK FACE ONE-THIRTY |  |
| 780 | 🕝 | U+1F55D | CLOCK FACE TWO-THIRTY |  |
| 781 | 🕞 | U+1F55E | CLOCK FACE THREE-THIRTY |  |
| 782 | 🕟 | U+1F55F | CLOCK FACE FOUR-THIRTY |  |
| 783 | 🕠 | U+1F560 | CLOCK FACE FIVE-THIRTY |  |
| 784 | 🕡 | U+1F561 | CLOCK FACE SIX-THIRTY |  |
| 785 | 🕢 | U+1F562 | CLOCK FACE SEVEN-THIRTY |  |
| 786 | 🕣 | U+1F563 | CLOCK FACE EIGHT-THIRTY |  |
| 787 | 🕤 | U+1F564 | CLOCK FACE NINE-THIRTY |  |
| 788 | 🕥 | U+1F565 | CLOCK FACE TEN-THIRTY |  |
| 789 | 🕦 | U+1F566 | CLOCK FACE ELEVEN-THIRTY |  |
| 790 | 🕧 | U+1F567 | CLOCK FACE TWELVE-THIRTY |  |
| 791 | 🕯 | U+1F56F | CANDLE |  |
| 792 | 🕰 | U+1F570 | MANTELPIECE CLOCK |  |
| 793 | 🕳 | U+1F573 | HOLE |  |
| 794 | 🕴 | U+1F574 | MAN IN BUSINESS SUIT LEVITATING |  |
| 795 | 🕵 | U+1F575 | SLEUTH OR SPY |  |
| 796 | 🕶 | U+1F576 | DARK SUNGLASSES |  |
| 797 | 🕷 | U+1F577 | SPIDER |  |
| 798 | 🕸 | U+1F578 | SPIDER WEB |  |
| 799 | 🕹 | U+1F579 | JOYSTICK |  |
| 800 | 🕺 | U+1F57A | MAN DANCING |  |
| 801 | 🖇 | U+1F587 | LINKED PAPERCLIPS |  |
| 802 | 🖊 | U+1F58A | LOWER LEFT BALLPOINT PEN |  |
| 803 | 🖋 | U+1F58B | LOWER LEFT FOUNTAIN PEN |  |
| 804 | 🖌 | U+1F58C | LOWER LEFT PAINTBRUSH |  |
| 805 | 🖍 | U+1F58D | LOWER LEFT CRAYON |  |
| 806 | 🖐 | U+1F590 | RAISED HAND WITH FINGERS SPLAYED |  |
| 807 | 🖕 | U+1F595 | REVERSED HAND WITH MIDDLE FINGER EXTENDED |  |
| 808 | 🖖 | U+1F596 | RAISED HAND WITH PART BETWEEN MIDDLE AND RING FINGERS |  |
| 809 | 🖤 | U+1F5A4 | BLACK HEART |  |
| 810 | 🖥 | U+1F5A5 | DESKTOP COMPUTER |  |
| 811 | 🖨 | U+1F5A8 | PRINTER |  |
| 812 | 🖱 | U+1F5B1 | THREE BUTTON MOUSE |  |
| 813 | 🖲 | U+1F5B2 | TRACKBALL |  |
| 814 | 🖼 | U+1F5BC | FRAME WITH PICTURE | 备选 · read_image |
| 815 | 🗂 | U+1F5C2 | CARD INDEX DIVIDERS |  |
| 816 | 🗃 | U+1F5C3 | CARD FILE BOX |  |
| 817 | 🗄 | U+1F5C4 | FILE CABINET |  |
| 818 | 🗑 | U+1F5D1 | WASTEBASKET |  |
| 819 | 🗒 | U+1F5D2 | SPIRAL NOTE PAD | 备选 · todo_write |
| 820 | 🗓 | U+1F5D3 | SPIRAL CALENDAR PAD |  |
| 821 | 🗜 | U+1F5DC | COMPRESSION |  |
| 822 | 🗝 | U+1F5DD | OLD KEY |  |
| 823 | 🗞 | U+1F5DE | ROLLED-UP NEWSPAPER |  |
| 824 | 🗡 | U+1F5E1 | DAGGER KNIFE |  |
| 825 | 🗣 | U+1F5E3 | SPEAKING HEAD IN SILHOUETTE |  |
| 826 | 🗨 | U+1F5E8 | LEFT SPEECH BUBBLE |  |
| 827 | 🗯 | U+1F5EF | RIGHT ANGER BUBBLE |  |
| 828 | 🗳 | U+1F5F3 | BALLOT BOX WITH BALLOT |  |
| 829 | 🗺 | U+1F5FA | WORLD MAP |  |
| 830 | 🗻 | U+1F5FB | MOUNT FUJI |  |
| 831 | 🗼 | U+1F5FC | TOKYO TOWER |  |
| 832 | 🗽 | U+1F5FD | STATUE OF LIBERTY |  |
| 833 | 🗾 | U+1F5FE | SILHOUETTE OF JAPAN |  |
| 834 | 🗿 | U+1F5FF | MOYAI |  |

<a id="sec-11"></a>
### 3.11 表情与人物（U+1F600–U+1F64F，80 个）

| # | 图标 | 码点 | Unicode 名称 | 卡片标记 |
|:---:|:---:|:---:|:---|:---|
| 835 | 😀 | U+1F600 | GRINNING FACE |  |
| 836 | 😁 | U+1F601 | GRINNING FACE WITH SMILING EYES |  |
| 837 | 😂 | U+1F602 | FACE WITH TEARS OF JOY |  |
| 838 | 😃 | U+1F603 | SMILING FACE WITH OPEN MOUTH |  |
| 839 | 😄 | U+1F604 | SMILING FACE WITH OPEN MOUTH AND SMILING EYES |  |
| 840 | 😅 | U+1F605 | SMILING FACE WITH OPEN MOUTH AND COLD SWEAT |  |
| 841 | 😆 | U+1F606 | SMILING FACE WITH OPEN MOUTH AND TIGHTLY-CLOSED EYES |  |
| 842 | 😇 | U+1F607 | SMILING FACE WITH HALO |  |
| 843 | 😈 | U+1F608 | SMILING FACE WITH HORNS |  |
| 844 | 😉 | U+1F609 | WINKING FACE |  |
| 845 | 😊 | U+1F60A | SMILING FACE WITH SMILING EYES |  |
| 846 | 😋 | U+1F60B | FACE SAVOURING DELICIOUS FOOD |  |
| 847 | 😌 | U+1F60C | RELIEVED FACE |  |
| 848 | 😍 | U+1F60D | SMILING FACE WITH HEART-SHAPED EYES |  |
| 849 | 😎 | U+1F60E | SMILING FACE WITH SUNGLASSES |  |
| 850 | 😏 | U+1F60F | SMIRKING FACE |  |
| 851 | 😐 | U+1F610 | NEUTRAL FACE |  |
| 852 | 😑 | U+1F611 | EXPRESSIONLESS FACE |  |
| 853 | 😒 | U+1F612 | UNAMUSED FACE |  |
| 854 | 😓 | U+1F613 | FACE WITH COLD SWEAT |  |
| 855 | 😔 | U+1F614 | PENSIVE FACE |  |
| 856 | 😕 | U+1F615 | CONFUSED FACE |  |
| 857 | 😖 | U+1F616 | CONFOUNDED FACE |  |
| 858 | 😗 | U+1F617 | KISSING FACE |  |
| 859 | 😘 | U+1F618 | FACE THROWING A KISS |  |
| 860 | 😙 | U+1F619 | KISSING FACE WITH SMILING EYES |  |
| 861 | 😚 | U+1F61A | KISSING FACE WITH CLOSED EYES |  |
| 862 | 😛 | U+1F61B | FACE WITH STUCK-OUT TONGUE |  |
| 863 | 😜 | U+1F61C | FACE WITH STUCK-OUT TONGUE AND WINKING EYE |  |
| 864 | 😝 | U+1F61D | FACE WITH STUCK-OUT TONGUE AND TIGHTLY-CLOSED EYES |  |
| 865 | 😞 | U+1F61E | DISAPPOINTED FACE |  |
| 866 | 😟 | U+1F61F | WORRIED FACE |  |
| 867 | 😠 | U+1F620 | ANGRY FACE |  |
| 868 | 😡 | U+1F621 | POUTING FACE |  |
| 869 | 😢 | U+1F622 | CRYING FACE |  |
| 870 | 😣 | U+1F623 | PERSEVERING FACE |  |
| 871 | 😤 | U+1F624 | FACE WITH LOOK OF TRIUMPH |  |
| 872 | 😥 | U+1F625 | DISAPPOINTED BUT RELIEVED FACE |  |
| 873 | 😦 | U+1F626 | FROWNING FACE WITH OPEN MOUTH |  |
| 874 | 😧 | U+1F627 | ANGUISHED FACE |  |
| 875 | 😨 | U+1F628 | FEARFUL FACE |  |
| 876 | 😩 | U+1F629 | WEARY FACE |  |
| 877 | 😪 | U+1F62A | SLEEPY FACE |  |
| 878 | 😫 | U+1F62B | TIRED FACE |  |
| 879 | 😬 | U+1F62C | GRIMACING FACE |  |
| 880 | 😭 | U+1F62D | LOUDLY CRYING FACE |  |
| 881 | 😮 | U+1F62E | FACE WITH OPEN MOUTH |  |
| 882 | 😯 | U+1F62F | HUSHED FACE |  |
| 883 | 😰 | U+1F630 | FACE WITH OPEN MOUTH AND COLD SWEAT |  |
| 884 | 😱 | U+1F631 | FACE SCREAMING IN FEAR |  |
| 885 | 😲 | U+1F632 | ASTONISHED FACE |  |
| 886 | 😳 | U+1F633 | FLUSHED FACE |  |
| 887 | 😴 | U+1F634 | SLEEPING FACE |  |
| 888 | 😵 | U+1F635 | DIZZY FACE |  |
| 889 | 😶 | U+1F636 | FACE WITHOUT MOUTH |  |
| 890 | 😷 | U+1F637 | FACE WITH MEDICAL MASK |  |
| 891 | 😸 | U+1F638 | GRINNING CAT FACE WITH SMILING EYES |  |
| 892 | 😹 | U+1F639 | CAT FACE WITH TEARS OF JOY |  |
| 893 | 😺 | U+1F63A | SMILING CAT FACE WITH OPEN MOUTH |  |
| 894 | 😻 | U+1F63B | SMILING CAT FACE WITH HEART-SHAPED EYES |  |
| 895 | 😼 | U+1F63C | CAT FACE WITH WRY SMILE |  |
| 896 | 😽 | U+1F63D | KISSING CAT FACE WITH CLOSED EYES |  |
| 897 | 😾 | U+1F63E | POUTING CAT FACE |  |
| 898 | 😿 | U+1F63F | CRYING CAT FACE |  |
| 899 | 🙀 | U+1F640 | WEARY CAT FACE |  |
| 900 | 🙁 | U+1F641 | SLIGHTLY FROWNING FACE |  |
| 901 | 🙂 | U+1F642 | SLIGHTLY SMILING FACE |  |
| 902 | 🙃 | U+1F643 | UPSIDE-DOWN FACE |  |
| 903 | 🙄 | U+1F644 | FACE WITH ROLLING EYES |  |
| 904 | 🙅 | U+1F645 | FACE WITH NO GOOD GESTURE |  |
| 905 | 🙆 | U+1F646 | FACE WITH OK GESTURE |  |
| 906 | 🙇 | U+1F647 | PERSON BOWING DEEPLY |  |
| 907 | 🙈 | U+1F648 | SEE-NO-EVIL MONKEY |  |
| 908 | 🙉 | U+1F649 | HEAR-NO-EVIL MONKEY |  |
| 909 | 🙊 | U+1F64A | SPEAK-NO-EVIL MONKEY |  |
| 910 | 🙋 | U+1F64B | HAPPY PERSON RAISING ONE HAND |  |
| 911 | 🙌 | U+1F64C | PERSON RAISING BOTH HANDS IN CELEBRATION |  |
| 912 | 🙍 | U+1F64D | PERSON FROWNING |  |
| 913 | 🙎 | U+1F64E | PERSON WITH POUTING FACE |  |
| 914 | 🙏 | U+1F64F | PERSON WITH FOLDED HANDS |  |

<a id="sec-12"></a>
### 3.12 交通与地点（U+1F680–U+1F6FF，106 个）

| # | 图标 | 码点 | Unicode 名称 | 卡片标记 |
|:---:|:---:|:---:|:---|:---|
| 915 | 🚀 | U+1F680 | ROCKET |  |
| 916 | 🚁 | U+1F681 | HELICOPTER |  |
| 917 | 🚂 | U+1F682 | STEAM LOCOMOTIVE |  |
| 918 | 🚃 | U+1F683 | RAILWAY CAR |  |
| 919 | 🚄 | U+1F684 | HIGH-SPEED TRAIN |  |
| 920 | 🚅 | U+1F685 | HIGH-SPEED TRAIN WITH BULLET NOSE |  |
| 921 | 🚆 | U+1F686 | TRAIN |  |
| 922 | 🚇 | U+1F687 | METRO |  |
| 923 | 🚈 | U+1F688 | LIGHT RAIL |  |
| 924 | 🚉 | U+1F689 | STATION |  |
| 925 | 🚊 | U+1F68A | TRAM |  |
| 926 | 🚋 | U+1F68B | TRAM CAR |  |
| 927 | 🚌 | U+1F68C | BUS |  |
| 928 | 🚍 | U+1F68D | ONCOMING BUS |  |
| 929 | 🚎 | U+1F68E | TROLLEYBUS |  |
| 930 | 🚏 | U+1F68F | BUS STOP |  |
| 931 | 🚐 | U+1F690 | MINIBUS |  |
| 932 | 🚑 | U+1F691 | AMBULANCE |  |
| 933 | 🚒 | U+1F692 | FIRE ENGINE |  |
| 934 | 🚓 | U+1F693 | POLICE CAR |  |
| 935 | 🚔 | U+1F694 | ONCOMING POLICE CAR |  |
| 936 | 🚕 | U+1F695 | TAXI |  |
| 937 | 🚖 | U+1F696 | ONCOMING TAXI |  |
| 938 | 🚗 | U+1F697 | AUTOMOBILE |  |
| 939 | 🚘 | U+1F698 | ONCOMING AUTOMOBILE |  |
| 940 | 🚙 | U+1F699 | RECREATIONAL VEHICLE |  |
| 941 | 🚚 | U+1F69A | DELIVERY TRUCK |  |
| 942 | 🚛 | U+1F69B | ARTICULATED LORRY |  |
| 943 | 🚜 | U+1F69C | TRACTOR |  |
| 944 | 🚝 | U+1F69D | MONORAIL |  |
| 945 | 🚞 | U+1F69E | MOUNTAIN RAILWAY |  |
| 946 | 🚟 | U+1F69F | SUSPENSION RAILWAY |  |
| 947 | 🚠 | U+1F6A0 | MOUNTAIN CABLEWAY |  |
| 948 | 🚡 | U+1F6A1 | AERIAL TRAMWAY |  |
| 949 | 🚢 | U+1F6A2 | SHIP |  |
| 950 | 🚣 | U+1F6A3 | ROWBOAT |  |
| 951 | 🚤 | U+1F6A4 | SPEEDBOAT |  |
| 952 | 🚥 | U+1F6A5 | HORIZONTAL TRAFFIC LIGHT |  |
| 953 | 🚦 | U+1F6A6 | VERTICAL TRAFFIC LIGHT |  |
| 954 | 🚧 | U+1F6A7 | CONSTRUCTION SIGN |  |
| 955 | 🚨 | U+1F6A8 | POLICE CARS REVOLVING LIGHT |  |
| 956 | 🚩 | U+1F6A9 | TRIANGULAR FLAG ON POST |  |
| 957 | 🚪 | U+1F6AA | DOOR |  |
| 958 | 🚫 | U+1F6AB | NO ENTRY SIGN |  |
| 959 | 🚬 | U+1F6AC | SMOKING SYMBOL |  |
| 960 | 🚭 | U+1F6AD | NO SMOKING SYMBOL |  |
| 961 | 🚮 | U+1F6AE | PUT LITTER IN ITS PLACE SYMBOL |  |
| 962 | 🚯 | U+1F6AF | DO NOT LITTER SYMBOL |  |
| 963 | 🚰 | U+1F6B0 | POTABLE WATER SYMBOL |  |
| 964 | 🚱 | U+1F6B1 | NON-POTABLE WATER SYMBOL |  |
| 965 | 🚲 | U+1F6B2 | BICYCLE |  |
| 966 | 🚳 | U+1F6B3 | NO BICYCLES |  |
| 967 | 🚴 | U+1F6B4 | BICYCLIST |  |
| 968 | 🚵 | U+1F6B5 | MOUNTAIN BICYCLIST |  |
| 969 | 🚶 | U+1F6B6 | PEDESTRIAN |  |
| 970 | 🚷 | U+1F6B7 | NO PEDESTRIANS |  |
| 971 | 🚸 | U+1F6B8 | CHILDREN CROSSING |  |
| 972 | 🚹 | U+1F6B9 | MENS SYMBOL |  |
| 973 | 🚺 | U+1F6BA | WOMENS SYMBOL |  |
| 974 | 🚻 | U+1F6BB | RESTROOM |  |
| 975 | 🚼 | U+1F6BC | BABY SYMBOL |  |
| 976 | 🚽 | U+1F6BD | TOILET |  |
| 977 | 🚾 | U+1F6BE | WATER CLOSET |  |
| 978 | 🚿 | U+1F6BF | SHOWER |  |
| 979 | 🛀 | U+1F6C0 | BATH |  |
| 980 | 🛁 | U+1F6C1 | BATHTUB |  |
| 981 | 🛂 | U+1F6C2 | PASSPORT CONTROL |  |
| 982 | 🛃 | U+1F6C3 | CUSTOMS |  |
| 983 | 🛄 | U+1F6C4 | BAGGAGE CLAIM |  |
| 984 | 🛅 | U+1F6C5 | LEFT LUGGAGE |  |
| 985 | 🛋 | U+1F6CB | COUCH AND LAMP |  |
| 986 | 🛌 | U+1F6CC | SLEEPING ACCOMMODATION |  |
| 987 | 🛍 | U+1F6CD | SHOPPING BAGS |  |
| 988 | 🛎 | U+1F6CE | BELLHOP BELL |  |
| 989 | 🛏 | U+1F6CF | BED |  |
| 990 | 🛐 | U+1F6D0 | PLACE OF WORSHIP |  |
| 991 | 🛑 | U+1F6D1 | OCTAGONAL SIGN |  |
| 992 | 🛒 | U+1F6D2 | SHOPPING TROLLEY |  |
| 993 | 🛕 | U+1F6D5 | HINDU TEMPLE |  |
| 994 | 🛖 | U+1F6D6 | HUT |  |
| 995 | 🛗 | U+1F6D7 | ELEVATOR |  |
| 996 | 🛘 | U+1F6D8 | （Unicode 名称未收录） | ⚠ 新码点，可能显示方框 |
| 997 | 🛜 | U+1F6DC | WIRELESS |  |
| 998 | 🛝 | U+1F6DD | PLAYGROUND SLIDE |  |
| 999 | 🛞 | U+1F6DE | WHEEL |  |
| 1000 | 🛟 | U+1F6DF | RING BUOY |  |
| 1001 | 🛠 | U+1F6E0 | HAMMER AND WRENCH |  |
| 1002 | 🛡 | U+1F6E1 | SHIELD |  |
| 1003 | 🛢 | U+1F6E2 | OIL DRUM |  |
| 1004 | 🛣 | U+1F6E3 | MOTORWAY |  |
| 1005 | 🛤 | U+1F6E4 | RAILWAY TRACK |  |
| 1006 | 🛥 | U+1F6E5 | MOTOR BOAT |  |
| 1007 | 🛩 | U+1F6E9 | SMALL AIRPLANE |  |
| 1008 | 🛫 | U+1F6EB | AIRPLANE DEPARTURE |  |
| 1009 | 🛬 | U+1F6EC | AIRPLANE ARRIVING |  |
| 1010 | 🛰 | U+1F6F0 | SATELLITE |  |
| 1011 | 🛳 | U+1F6F3 | PASSENGER SHIP |  |
| 1012 | 🛴 | U+1F6F4 | SCOOTER |  |
| 1013 | 🛵 | U+1F6F5 | MOTOR SCOOTER |  |
| 1014 | 🛶 | U+1F6F6 | CANOE |  |
| 1015 | 🛷 | U+1F6F7 | SLED |  |
| 1016 | 🛸 | U+1F6F8 | FLYING SAUCER |  |
| 1017 | 🛹 | U+1F6F9 | SKATEBOARD |  |
| 1018 | 🛺 | U+1F6FA | AUTO RICKSHAW |  |
| 1019 | 🛻 | U+1F6FB | PICKUP TRUCK |  |
| 1020 | 🛼 | U+1F6FC | ROLLER SKATE |  |

<a id="sec-13"></a>
### 3.13 补充符号（U+1F700–U+1F8FF，13 个）

| # | 图标 | 码点 | Unicode 名称 | 卡片标记 |
|:---:|:---:|:---:|:---|:---|
| 1021 | 🟠 | U+1F7E0 | LARGE ORANGE CIRCLE |  |
| 1022 | 🟡 | U+1F7E1 | LARGE YELLOW CIRCLE |  |
| 1023 | 🟢 | U+1F7E2 | LARGE GREEN CIRCLE |  |
| 1024 | 🟣 | U+1F7E3 | LARGE PURPLE CIRCLE |  |
| 1025 | 🟤 | U+1F7E4 | LARGE BROWN CIRCLE |  |
| 1026 | 🟥 | U+1F7E5 | LARGE RED SQUARE |  |
| 1027 | 🟦 | U+1F7E6 | LARGE BLUE SQUARE |  |
| 1028 | 🟧 | U+1F7E7 | LARGE ORANGE SQUARE |  |
| 1029 | 🟨 | U+1F7E8 | LARGE YELLOW SQUARE |  |
| 1030 | 🟩 | U+1F7E9 | LARGE GREEN SQUARE |  |
| 1031 | 🟪 | U+1F7EA | LARGE PURPLE SQUARE |  |
| 1032 | 🟫 | U+1F7EB | LARGE BROWN SQUARE |  |
| 1033 | 🟰 | U+1F7F0 | HEAVY EQUALS SIGN |  |

<a id="sec-14"></a>
### 3.14 手势、人物与运动（U+1F900–U+1F946，57 个）

| # | 图标 | 码点 | Unicode 名称 | 卡片标记 |
|:---:|:---:|:---:|:---|:---|
| 1034 | 🤌 | U+1F90C | PINCHED FINGERS |  |
| 1035 | 🤍 | U+1F90D | WHITE HEART |  |
| 1036 | 🤎 | U+1F90E | BROWN HEART |  |
| 1037 | 🤏 | U+1F90F | PINCHING HAND |  |
| 1038 | 🤐 | U+1F910 | ZIPPER-MOUTH FACE |  |
| 1039 | 🤑 | U+1F911 | MONEY-MOUTH FACE |  |
| 1040 | 🤒 | U+1F912 | FACE WITH THERMOMETER |  |
| 1041 | 🤓 | U+1F913 | NERD FACE |  |
| 1042 | 🤔 | U+1F914 | THINKING FACE |  |
| 1043 | 🤕 | U+1F915 | FACE WITH HEAD-BANDAGE |  |
| 1044 | 🤖 | U+1F916 | ROBOT FACE | 现行 · think |
| 1045 | 🤗 | U+1F917 | HUGGING FACE |  |
| 1046 | 🤘 | U+1F918 | SIGN OF THE HORNS |  |
| 1047 | 🤙 | U+1F919 | CALL ME HAND |  |
| 1048 | 🤚 | U+1F91A | RAISED BACK OF HAND |  |
| 1049 | 🤛 | U+1F91B | LEFT-FACING FIST |  |
| 1050 | 🤜 | U+1F91C | RIGHT-FACING FIST |  |
| 1051 | 🤝 | U+1F91D | HANDSHAKE |  |
| 1052 | 🤞 | U+1F91E | HAND WITH INDEX AND MIDDLE FINGERS CROSSED |  |
| 1053 | 🤟 | U+1F91F | I LOVE YOU HAND SIGN |  |
| 1054 | 🤠 | U+1F920 | FACE WITH COWBOY HAT |  |
| 1055 | 🤡 | U+1F921 | CLOWN FACE |  |
| 1056 | 🤢 | U+1F922 | NAUSEATED FACE |  |
| 1057 | 🤣 | U+1F923 | ROLLING ON THE FLOOR LAUGHING |  |
| 1058 | 🤤 | U+1F924 | DROOLING FACE |  |
| 1059 | 🤥 | U+1F925 | LYING FACE |  |
| 1060 | 🤦 | U+1F926 | FACE PALM |  |
| 1061 | 🤧 | U+1F927 | SNEEZING FACE |  |
| 1062 | 🤨 | U+1F928 | FACE WITH ONE EYEBROW RAISED |  |
| 1063 | 🤩 | U+1F929 | GRINNING FACE WITH STAR EYES |  |
| 1064 | 🤪 | U+1F92A | GRINNING FACE WITH ONE LARGE AND ONE SMALL EYE |  |
| 1065 | 🤫 | U+1F92B | FACE WITH FINGER COVERING CLOSED LIPS |  |
| 1066 | 🤬 | U+1F92C | SERIOUS FACE WITH SYMBOLS COVERING MOUTH |  |
| 1067 | 🤭 | U+1F92D | SMILING FACE WITH SMILING EYES AND HAND COVERING MOUTH |  |
| 1068 | 🤮 | U+1F92E | FACE WITH OPEN MOUTH VOMITING |  |
| 1069 | 🤯 | U+1F92F | SHOCKED FACE WITH EXPLODING HEAD |  |
| 1070 | 🤰 | U+1F930 | PREGNANT WOMAN |  |
| 1071 | 🤱 | U+1F931 | BREAST-FEEDING |  |
| 1072 | 🤲 | U+1F932 | PALMS UP TOGETHER |  |
| 1073 | 🤳 | U+1F933 | SELFIE |  |
| 1074 | 🤴 | U+1F934 | PRINCE |  |
| 1075 | 🤵 | U+1F935 | MAN IN TUXEDO |  |
| 1076 | 🤶 | U+1F936 | MOTHER CHRISTMAS |  |
| 1077 | 🤷 | U+1F937 | SHRUG |  |
| 1078 | 🤸 | U+1F938 | PERSON DOING CARTWHEEL |  |
| 1079 | 🤹 | U+1F939 | JUGGLING |  |
| 1080 | 🤺 | U+1F93A | FENCER |  |
| 1081 | 🤼 | U+1F93C | WRESTLERS |  |
| 1082 | 🤽 | U+1F93D | WATER POLO |  |
| 1083 | 🤾 | U+1F93E | HANDBALL |  |
| 1084 | 🤿 | U+1F93F | DIVING MASK |  |
| 1085 | 🥀 | U+1F940 | WILTED FLOWER |  |
| 1086 | 🥁 | U+1F941 | DRUM WITH DRUMSTICKS |  |
| 1087 | 🥂 | U+1F942 | CLINKING GLASSES |  |
| 1088 | 🥃 | U+1F943 | TUMBLER GLASS |  |
| 1089 | 🥄 | U+1F944 | SPOON |  |
| 1090 | 🥅 | U+1F945 | GOAL NET |  |

<a id="sec-15"></a>
### 3.15 工具、物件与生活（U+1F947–U+1F9FF，185 个）

| # | 图标 | 码点 | Unicode 名称 | 卡片标记 |
|:---:|:---:|:---:|:---|:---|
| 1091 | 🥇 | U+1F947 | FIRST PLACE MEDAL |  |
| 1092 | 🥈 | U+1F948 | SECOND PLACE MEDAL |  |
| 1093 | 🥉 | U+1F949 | THIRD PLACE MEDAL |  |
| 1094 | 🥊 | U+1F94A | BOXING GLOVE |  |
| 1095 | 🥋 | U+1F94B | MARTIAL ARTS UNIFORM |  |
| 1096 | 🥌 | U+1F94C | CURLING STONE |  |
| 1097 | 🥍 | U+1F94D | LACROSSE STICK AND BALL |  |
| 1098 | 🥎 | U+1F94E | SOFTBALL |  |
| 1099 | 🥏 | U+1F94F | FLYING DISC |  |
| 1100 | 🥐 | U+1F950 | CROISSANT |  |
| 1101 | 🥑 | U+1F951 | AVOCADO |  |
| 1102 | 🥒 | U+1F952 | CUCUMBER |  |
| 1103 | 🥓 | U+1F953 | BACON |  |
| 1104 | 🥔 | U+1F954 | POTATO |  |
| 1105 | 🥕 | U+1F955 | CARROT |  |
| 1106 | 🥖 | U+1F956 | BAGUETTE BREAD |  |
| 1107 | 🥗 | U+1F957 | GREEN SALAD |  |
| 1108 | 🥘 | U+1F958 | SHALLOW PAN OF FOOD |  |
| 1109 | 🥙 | U+1F959 | STUFFED FLATBREAD |  |
| 1110 | 🥚 | U+1F95A | EGG |  |
| 1111 | 🥛 | U+1F95B | GLASS OF MILK |  |
| 1112 | 🥜 | U+1F95C | PEANUTS |  |
| 1113 | 🥝 | U+1F95D | KIWIFRUIT |  |
| 1114 | 🥞 | U+1F95E | PANCAKES |  |
| 1115 | 🥟 | U+1F95F | DUMPLING |  |
| 1116 | 🥠 | U+1F960 | FORTUNE COOKIE |  |
| 1117 | 🥡 | U+1F961 | TAKEOUT BOX |  |
| 1118 | 🥢 | U+1F962 | CHOPSTICKS |  |
| 1119 | 🥣 | U+1F963 | BOWL WITH SPOON |  |
| 1120 | 🥤 | U+1F964 | CUP WITH STRAW |  |
| 1121 | 🥥 | U+1F965 | COCONUT |  |
| 1122 | 🥦 | U+1F966 | BROCCOLI |  |
| 1123 | 🥧 | U+1F967 | PIE |  |
| 1124 | 🥨 | U+1F968 | PRETZEL |  |
| 1125 | 🥩 | U+1F969 | CUT OF MEAT |  |
| 1126 | 🥪 | U+1F96A | SANDWICH |  |
| 1127 | 🥫 | U+1F96B | CANNED FOOD |  |
| 1128 | 🥬 | U+1F96C | LEAFY GREEN |  |
| 1129 | 🥭 | U+1F96D | MANGO |  |
| 1130 | 🥮 | U+1F96E | MOON CAKE |  |
| 1131 | 🥯 | U+1F96F | BAGEL |  |
| 1132 | 🥰 | U+1F970 | SMILING FACE WITH SMILING EYES AND THREE HEARTS |  |
| 1133 | 🥱 | U+1F971 | YAWNING FACE |  |
| 1134 | 🥲 | U+1F972 | SMILING FACE WITH TEAR |  |
| 1135 | 🥳 | U+1F973 | FACE WITH PARTY HORN AND PARTY HAT |  |
| 1136 | 🥴 | U+1F974 | FACE WITH UNEVEN EYES AND WAVY MOUTH |  |
| 1137 | 🥵 | U+1F975 | OVERHEATED FACE |  |
| 1138 | 🥶 | U+1F976 | FREEZING FACE |  |
| 1139 | 🥷 | U+1F977 | NINJA |  |
| 1140 | 🥸 | U+1F978 | DISGUISED FACE |  |
| 1141 | 🥹 | U+1F979 | FACE HOLDING BACK TEARS |  |
| 1142 | 🥺 | U+1F97A | FACE WITH PLEADING EYES |  |
| 1143 | 🥻 | U+1F97B | SARI |  |
| 1144 | 🥼 | U+1F97C | LAB COAT |  |
| 1145 | 🥽 | U+1F97D | GOGGLES |  |
| 1146 | 🥾 | U+1F97E | HIKING BOOT |  |
| 1147 | 🥿 | U+1F97F | FLAT SHOE |  |
| 1148 | 🦀 | U+1F980 | CRAB |  |
| 1149 | 🦁 | U+1F981 | LION FACE |  |
| 1150 | 🦂 | U+1F982 | SCORPION |  |
| 1151 | 🦃 | U+1F983 | TURKEY |  |
| 1152 | 🦄 | U+1F984 | UNICORN FACE |  |
| 1153 | 🦅 | U+1F985 | EAGLE |  |
| 1154 | 🦆 | U+1F986 | DUCK |  |
| 1155 | 🦇 | U+1F987 | BAT |  |
| 1156 | 🦈 | U+1F988 | SHARK |  |
| 1157 | 🦉 | U+1F989 | OWL |  |
| 1158 | 🦊 | U+1F98A | FOX FACE |  |
| 1159 | 🦋 | U+1F98B | BUTTERFLY |  |
| 1160 | 🦌 | U+1F98C | DEER |  |
| 1161 | 🦍 | U+1F98D | GORILLA |  |
| 1162 | 🦎 | U+1F98E | LIZARD |  |
| 1163 | 🦏 | U+1F98F | RHINOCEROS |  |
| 1164 | 🦐 | U+1F990 | SHRIMP |  |
| 1165 | 🦑 | U+1F991 | SQUID |  |
| 1166 | 🦒 | U+1F992 | GIRAFFE FACE |  |
| 1167 | 🦓 | U+1F993 | ZEBRA FACE |  |
| 1168 | 🦔 | U+1F994 | HEDGEHOG |  |
| 1169 | 🦕 | U+1F995 | SAUROPOD |  |
| 1170 | 🦖 | U+1F996 | T-REX |  |
| 1171 | 🦗 | U+1F997 | CRICKET |  |
| 1172 | 🦘 | U+1F998 | KANGAROO |  |
| 1173 | 🦙 | U+1F999 | LLAMA |  |
| 1174 | 🦚 | U+1F99A | PEACOCK |  |
| 1175 | 🦛 | U+1F99B | HIPPOPOTAMUS |  |
| 1176 | 🦜 | U+1F99C | PARROT |  |
| 1177 | 🦝 | U+1F99D | RACCOON |  |
| 1178 | 🦞 | U+1F99E | LOBSTER |  |
| 1179 | 🦟 | U+1F99F | MOSQUITO |  |
| 1180 | 🦠 | U+1F9A0 | MICROBE |  |
| 1181 | 🦡 | U+1F9A1 | BADGER |  |
| 1182 | 🦢 | U+1F9A2 | SWAN |  |
| 1183 | 🦣 | U+1F9A3 | MAMMOTH |  |
| 1184 | 🦤 | U+1F9A4 | DODO |  |
| 1185 | 🦥 | U+1F9A5 | SLOTH |  |
| 1186 | 🦦 | U+1F9A6 | OTTER |  |
| 1187 | 🦧 | U+1F9A7 | ORANGUTAN |  |
| 1188 | 🦨 | U+1F9A8 | SKUNK |  |
| 1189 | 🦩 | U+1F9A9 | FLAMINGO |  |
| 1190 | 🦪 | U+1F9AA | OYSTER |  |
| 1191 | 🦫 | U+1F9AB | BEAVER |  |
| 1192 | 🦬 | U+1F9AC | BISON |  |
| 1193 | 🦭 | U+1F9AD | SEAL |  |
| 1194 | 🦮 | U+1F9AE | GUIDE DOG |  |
| 1195 | 🦯 | U+1F9AF | PROBING CANE |  |
| 1196 | 🦰 | U+1F9B0 | EMOJI COMPONENT RED HAIR |  |
| 1197 | 🦱 | U+1F9B1 | EMOJI COMPONENT CURLY HAIR |  |
| 1198 | 🦲 | U+1F9B2 | EMOJI COMPONENT BALD |  |
| 1199 | 🦳 | U+1F9B3 | EMOJI COMPONENT WHITE HAIR |  |
| 1200 | 🦴 | U+1F9B4 | BONE |  |
| 1201 | 🦵 | U+1F9B5 | LEG |  |
| 1202 | 🦶 | U+1F9B6 | FOOT |  |
| 1203 | 🦷 | U+1F9B7 | TOOTH |  |
| 1204 | 🦸 | U+1F9B8 | SUPERHERO |  |
| 1205 | 🦹 | U+1F9B9 | SUPERVILLAIN |  |
| 1206 | 🦺 | U+1F9BA | SAFETY VEST |  |
| 1207 | 🦻 | U+1F9BB | EAR WITH HEARING AID |  |
| 1208 | 🦼 | U+1F9BC | MOTORIZED WHEELCHAIR |  |
| 1209 | 🦽 | U+1F9BD | MANUAL WHEELCHAIR |  |
| 1210 | 🦾 | U+1F9BE | MECHANICAL ARM |  |
| 1211 | 🦿 | U+1F9BF | MECHANICAL LEG |  |
| 1212 | 🧀 | U+1F9C0 | CHEESE WEDGE |  |
| 1213 | 🧁 | U+1F9C1 | CUPCAKE |  |
| 1214 | 🧂 | U+1F9C2 | SALT SHAKER |  |
| 1215 | 🧃 | U+1F9C3 | BEVERAGE BOX |  |
| 1216 | 🧄 | U+1F9C4 | GARLIC |  |
| 1217 | 🧅 | U+1F9C5 | ONION |  |
| 1218 | 🧆 | U+1F9C6 | FALAFEL |  |
| 1219 | 🧇 | U+1F9C7 | WAFFLE |  |
| 1220 | 🧈 | U+1F9C8 | BUTTER |  |
| 1221 | 🧉 | U+1F9C9 | MATE DRINK |  |
| 1222 | 🧊 | U+1F9CA | ICE CUBE |  |
| 1223 | 🧋 | U+1F9CB | BUBBLE TEA |  |
| 1224 | 🧌 | U+1F9CC | TROLL |  |
| 1225 | 🧍 | U+1F9CD | STANDING PERSON |  |
| 1226 | 🧎 | U+1F9CE | KNEELING PERSON |  |
| 1227 | 🧏 | U+1F9CF | DEAF PERSON |  |
| 1228 | 🧐 | U+1F9D0 | FACE WITH MONOCLE |  |
| 1229 | 🧑 | U+1F9D1 | ADULT |  |
| 1230 | 🧒 | U+1F9D2 | CHILD |  |
| 1231 | 🧓 | U+1F9D3 | OLDER ADULT |  |
| 1232 | 🧔 | U+1F9D4 | BEARDED PERSON |  |
| 1233 | 🧕 | U+1F9D5 | PERSON WITH HEADSCARF |  |
| 1234 | 🧖 | U+1F9D6 | PERSON IN STEAMY ROOM |  |
| 1235 | 🧗 | U+1F9D7 | PERSON CLIMBING |  |
| 1236 | 🧘 | U+1F9D8 | PERSON IN LOTUS POSITION |  |
| 1237 | 🧙 | U+1F9D9 | MAGE |  |
| 1238 | 🧚 | U+1F9DA | FAIRY |  |
| 1239 | 🧛 | U+1F9DB | VAMPIRE |  |
| 1240 | 🧜 | U+1F9DC | MERPERSON |  |
| 1241 | 🧝 | U+1F9DD | ELF |  |
| 1242 | 🧞 | U+1F9DE | GENIE |  |
| 1243 | 🧟 | U+1F9DF | ZOMBIE |  |
| 1244 | 🧠 | U+1F9E0 | BRAIN | 现行 · Thinking |
| 1245 | 🧡 | U+1F9E1 | ORANGE HEART |  |
| 1246 | 🧢 | U+1F9E2 | BILLED CAP |  |
| 1247 | 🧣 | U+1F9E3 | SCARF |  |
| 1248 | 🧤 | U+1F9E4 | GLOVES |  |
| 1249 | 🧥 | U+1F9E5 | COAT |  |
| 1250 | 🧦 | U+1F9E6 | SOCKS |  |
| 1251 | 🧧 | U+1F9E7 | RED GIFT ENVELOPE |  |
| 1252 | 🧨 | U+1F9E8 | FIRECRACKER |  |
| 1253 | 🧩 | U+1F9E9 | JIGSAW PUZZLE PIECE | 现行 · other（未知/AskUserQuestion） |
| 1254 | 🧪 | U+1F9EA | TEST TUBE |  |
| 1255 | 🧫 | U+1F9EB | PETRI DISH |  |
| 1256 | 🧬 | U+1F9EC | DNA DOUBLE HELIX |  |
| 1257 | 🧭 | U+1F9ED | COMPASS |  |
| 1258 | 🧮 | U+1F9EE | ABACUS |  |
| 1259 | 🧯 | U+1F9EF | FIRE EXTINGUISHER |  |
| 1260 | 🧰 | U+1F9F0 | TOOLBOX | 备选 · MCP 服务器 |
| 1261 | 🧱 | U+1F9F1 | BRICK |  |
| 1262 | 🧲 | U+1F9F2 | MAGNET |  |
| 1263 | 🧳 | U+1F9F3 | LUGGAGE |  |
| 1264 | 🧴 | U+1F9F4 | LOTION BOTTLE |  |
| 1265 | 🧵 | U+1F9F5 | SPOOL OF THREAD |  |
| 1266 | 🧶 | U+1F9F6 | BALL OF YARN |  |
| 1267 | 🧷 | U+1F9F7 | SAFETY PIN |  |
| 1268 | 🧸 | U+1F9F8 | TEDDY BEAR |  |
| 1269 | 🧹 | U+1F9F9 | BROOM |  |
| 1270 | 🧺 | U+1F9FA | BASKET |  |
| 1271 | 🧻 | U+1F9FB | ROLL OF PAPER |  |
| 1272 | 🧼 | U+1F9FC | BAR OF SOAP |  |
| 1273 | 🧽 | U+1F9FD | SPONGE |  |
| 1274 | 🧾 | U+1F9FE | RECEIPT |  |
| 1275 | 🧿 | U+1F9FF | NAZAR AMULET |  |

<a id="sec-16"></a>
### 3.16 新增物件与符号（U+1FA00–U+1FAFF，120 个）

| # | 图标 | 码点 | Unicode 名称 | 卡片标记 |
|:---:|:---:|:---:|:---|:---|
| 1276 | 🩰 | U+1FA70 | BALLET SHOES |  |
| 1277 | 🩱 | U+1FA71 | ONE-PIECE SWIMSUIT |  |
| 1278 | 🩲 | U+1FA72 | BRIEFS |  |
| 1279 | 🩳 | U+1FA73 | SHORTS |  |
| 1280 | 🩴 | U+1FA74 | THONG SANDAL |  |
| 1281 | 🩵 | U+1FA75 | LIGHT BLUE HEART |  |
| 1282 | 🩶 | U+1FA76 | GREY HEART |  |
| 1283 | 🩷 | U+1FA77 | PINK HEART |  |
| 1284 | 🩸 | U+1FA78 | DROP OF BLOOD |  |
| 1285 | 🩹 | U+1FA79 | ADHESIVE BANDAGE |  |
| 1286 | 🩺 | U+1FA7A | STETHOSCOPE |  |
| 1287 | 🩻 | U+1FA7B | X-RAY |  |
| 1288 | 🩼 | U+1FA7C | CRUTCH |  |
| 1289 | 🪀 | U+1FA80 | YO-YO |  |
| 1290 | 🪁 | U+1FA81 | KITE |  |
| 1291 | 🪂 | U+1FA82 | PARACHUTE |  |
| 1292 | 🪃 | U+1FA83 | BOOMERANG |  |
| 1293 | 🪄 | U+1FA84 | MAGIC WAND |  |
| 1294 | 🪅 | U+1FA85 | PINATA |  |
| 1295 | 🪆 | U+1FA86 | NESTING DOLLS |  |
| 1296 | 🪇 | U+1FA87 | MARACAS |  |
| 1297 | 🪈 | U+1FA88 | FLUTE |  |
| 1298 | 🪉 | U+1FA89 | （Unicode 名称未收录） | ⚠ 新码点，可能显示方框 |
| 1299 | 🪊 | U+1FA8A | （Unicode 名称未收录） | ⚠ 新码点，可能显示方框 |
| 1300 | 🪎 | U+1FA8E | （Unicode 名称未收录） | ⚠ 新码点，可能显示方框 |
| 1301 | 🪏 | U+1FA8F | （Unicode 名称未收录） | ⚠ 新码点，可能显示方框 |
| 1302 | 🪐 | U+1FA90 | RINGED PLANET |  |
| 1303 | 🪑 | U+1FA91 | CHAIR |  |
| 1304 | 🪒 | U+1FA92 | RAZOR |  |
| 1305 | 🪓 | U+1FA93 | AXE |  |
| 1306 | 🪔 | U+1FA94 | DIYA LAMP |  |
| 1307 | 🪕 | U+1FA95 | BANJO |  |
| 1308 | 🪖 | U+1FA96 | MILITARY HELMET |  |
| 1309 | 🪗 | U+1FA97 | ACCORDION |  |
| 1310 | 🪘 | U+1FA98 | LONG DRUM |  |
| 1311 | 🪙 | U+1FA99 | COIN |  |
| 1312 | 🪚 | U+1FA9A | CARPENTRY SAW |  |
| 1313 | 🪛 | U+1FA9B | SCREWDRIVER |  |
| 1314 | 🪜 | U+1FA9C | LADDER |  |
| 1315 | 🪝 | U+1FA9D | HOOK |  |
| 1316 | 🪞 | U+1FA9E | MIRROR |  |
| 1317 | 🪟 | U+1FA9F | WINDOW |  |
| 1318 | 🪠 | U+1FAA0 | PLUNGER |  |
| 1319 | 🪡 | U+1FAA1 | SEWING NEEDLE |  |
| 1320 | 🪢 | U+1FAA2 | KNOT |  |
| 1321 | 🪣 | U+1FAA3 | BUCKET |  |
| 1322 | 🪤 | U+1FAA4 | MOUSE TRAP |  |
| 1323 | 🪥 | U+1FAA5 | TOOTHBRUSH |  |
| 1324 | 🪦 | U+1FAA6 | HEADSTONE |  |
| 1325 | 🪧 | U+1FAA7 | PLACARD |  |
| 1326 | 🪨 | U+1FAA8 | ROCK |  |
| 1327 | 🪩 | U+1FAA9 | MIRROR BALL |  |
| 1328 | 🪪 | U+1FAAA | IDENTIFICATION CARD |  |
| 1329 | 🪫 | U+1FAAB | LOW BATTERY |  |
| 1330 | 🪬 | U+1FAAC | HAMSA |  |
| 1331 | 🪭 | U+1FAAD | FOLDING HAND FAN |  |
| 1332 | 🪮 | U+1FAAE | HAIR PICK |  |
| 1333 | 🪯 | U+1FAAF | KHANDA |  |
| 1334 | 🪰 | U+1FAB0 | FLY |  |
| 1335 | 🪱 | U+1FAB1 | WORM |  |
| 1336 | 🪲 | U+1FAB2 | BEETLE |  |
| 1337 | 🪳 | U+1FAB3 | COCKROACH |  |
| 1338 | 🪴 | U+1FAB4 | POTTED PLANT |  |
| 1339 | 🪵 | U+1FAB5 | WOOD |  |
| 1340 | 🪶 | U+1FAB6 | FEATHER |  |
| 1341 | 🪷 | U+1FAB7 | LOTUS |  |
| 1342 | 🪸 | U+1FAB8 | CORAL |  |
| 1343 | 🪹 | U+1FAB9 | EMPTY NEST |  |
| 1344 | 🪺 | U+1FABA | NEST WITH EGGS |  |
| 1345 | 🪻 | U+1FABB | HYACINTH |  |
| 1346 | 🪼 | U+1FABC | JELLYFISH |  |
| 1347 | 🪽 | U+1FABD | WING |  |
| 1348 | 🪾 | U+1FABE | （Unicode 名称未收录） | ⚠ 新码点，可能显示方框 |
| 1349 | 🪿 | U+1FABF | GOOSE |  |
| 1350 | 🫀 | U+1FAC0 | ANATOMICAL HEART |  |
| 1351 | 🫁 | U+1FAC1 | LUNGS |  |
| 1352 | 🫂 | U+1FAC2 | PEOPLE HUGGING |  |
| 1353 | 🫃 | U+1FAC3 | PREGNANT MAN |  |
| 1354 | 🫄 | U+1FAC4 | PREGNANT PERSON |  |
| 1355 | 🫅 | U+1FAC5 | PERSON WITH CROWN |  |
| 1356 | 🫆 | U+1FAC6 | （Unicode 名称未收录） | ⚠ 新码点，可能显示方框 |
| 1357 | 🫈 | U+1FAC8 | （Unicode 名称未收录） | ⚠ 新码点，可能显示方框 |
| 1358 | 🫍 | U+1FACD | （Unicode 名称未收录） | ⚠ 新码点，可能显示方框 |
| 1359 | 🫎 | U+1FACE | MOOSE |  |
| 1360 | 🫏 | U+1FACF | DONKEY |  |
| 1361 | 🫐 | U+1FAD0 | BLUEBERRIES |  |
| 1362 | 🫑 | U+1FAD1 | BELL PEPPER |  |
| 1363 | 🫒 | U+1FAD2 | OLIVE |  |
| 1364 | 🫓 | U+1FAD3 | FLATBREAD |  |
| 1365 | 🫔 | U+1FAD4 | TAMALE |  |
| 1366 | 🫕 | U+1FAD5 | FONDUE |  |
| 1367 | 🫖 | U+1FAD6 | TEAPOT |  |
| 1368 | 🫗 | U+1FAD7 | POURING LIQUID |  |
| 1369 | 🫘 | U+1FAD8 | BEANS |  |
| 1370 | 🫙 | U+1FAD9 | JAR |  |
| 1371 | 🫚 | U+1FADA | GINGER ROOT |  |
| 1372 | 🫛 | U+1FADB | PEA POD |  |
| 1373 | 🫜 | U+1FADC | （Unicode 名称未收录） | ⚠ 新码点，可能显示方框 |
| 1374 | 🫟 | U+1FADF | （Unicode 名称未收录） | ⚠ 新码点，可能显示方框 |
| 1375 | 🫠 | U+1FAE0 | MELTING FACE |  |
| 1376 | 🫡 | U+1FAE1 | SALUTING FACE |  |
| 1377 | 🫢 | U+1FAE2 | FACE WITH OPEN EYES AND HAND OVER MOUTH |  |
| 1378 | 🫣 | U+1FAE3 | FACE WITH PEEKING EYE |  |
| 1379 | 🫤 | U+1FAE4 | FACE WITH DIAGONAL MOUTH |  |
| 1380 | 🫥 | U+1FAE5 | DOTTED LINE FACE |  |
| 1381 | 🫦 | U+1FAE6 | BITING LIP |  |
| 1382 | 🫧 | U+1FAE7 | BUBBLES |  |
| 1383 | 🫨 | U+1FAE8 | SHAKING FACE |  |
| 1384 | 🫩 | U+1FAE9 | （Unicode 名称未收录） | ⚠ 新码点，可能显示方框 |
| 1385 | 🫪 | U+1FAEA | （Unicode 名称未收录） | ⚠ 新码点，可能显示方框 |
| 1386 | 🫯 | U+1FAEF | （Unicode 名称未收录） | ⚠ 新码点，可能显示方框 |
| 1387 | 🫰 | U+1FAF0 | HAND WITH INDEX FINGER AND THUMB CROSSED |  |
| 1388 | 🫱 | U+1FAF1 | RIGHTWARDS HAND |  |
| 1389 | 🫲 | U+1FAF2 | LEFTWARDS HAND |  |
| 1390 | 🫳 | U+1FAF3 | PALM DOWN HAND |  |
| 1391 | 🫴 | U+1FAF4 | PALM UP HAND |  |
| 1392 | 🫵 | U+1FAF5 | INDEX POINTING AT THE VIEWER |  |
| 1393 | 🫶 | U+1FAF6 | HEART HANDS |  |
| 1394 | 🫷 | U+1FAF7 | LEFTWARDS PUSHING HAND |  |
| 1395 | 🫸 | U+1FAF8 | RIGHTWARDS PUSHING HAND |  |

---

## 4. 维护口径

- 本目录是单码点全集，不等于「全部彩色 emoji」：带 ZWJ、肤色、旗帜、键帽的组合没有列入；如果以后卡片需要组合字形，另开目录。
- 字体升级后需重跑枚举并更新字体 sha256；旧目录里的码点仍然有效，但新增/改名需要复核。
- 本文档只服务图标挑选，不定义卡片分派；具体卡型与分派以 `vendors/dsh/factory.py` 为准。
- `U+1F441` 的实际显示值附加 `U+FE0F`；目录仍以单码点列条目，这是唯一 VS16 例外。
- 重生成入口：先跑 `fc-query --format '%{charset}' assets/fonts/Noto彩色Emoji/NotoColorEmoji.ttf` 取 charset，再按 §1 与本节口径过滤；字体 sha256 变化时同步更新文首。

*创建于 2026-11-21 | 字体 sha256 `72a635cb3d2f3524…` | 单码点 1,395 个*
