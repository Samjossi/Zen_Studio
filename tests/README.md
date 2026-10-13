# tests/ 测试目录说明

门禁入口：`bash scripts/check.sh`（隐私扫描 + ruff + pytest 全量），提交前必跑且必须全绿。

## 公共约定

- GUI 用例一律 offscreen 平台（`conftest.py` 注入 `QT_QPA_PLATFORM=offscreen`），不起真实窗口；
- 配置文件读写重定向到 `./.temp/` 隔离（如 `test_notification.py` 的 `isolated_settings` 装置），严禁污染真实 `config/settings.json`；
- 不起真实 CLI/后端：注册表、ACP 连接等一律 monkeypatch 伪造；
- 临时产物一律落 `./.temp/`，禁用系统临时目录。

## 硬性纪律：测试禁止真实出声

音频播放链路（`gui/sound_player.py` 的 `_play` / `play_system`、媒体查看器播放）**必须打桩**，只允许断言"调用了哪个文件/不抛异常"，不允许真的向声卡输出。

**为什么**：offscreen 只关 GUI 显示，不关音频后端——在有声卡的开发机上，未打桩的播放会真实出声。曾有一条"验证播放不崩溃"的用例（`test_notification.py` 的 `test_sound_real_file_no_crash`）未打桩，导致每次跑门禁都外放一声 `done.wav`（= `bip-bop-01.wav` 音色）；由于门禁按纪律在任务收尾（提交前）执行，用户总在任务快结束时听到这声来路不明的提示音，且设置中心关不掉它（测试走隔离配置，不读用户设置）。排查结论详见 `文档/审计报告/2026-1013-1152_任务收尾额外提示音bip-bop-01溯源审计报告.md`。

**现行处理方式**：该用例保留（守护"播放路径不崩溃"），但 `QSoundEffect` 换为假后端 `_FakeEffect`——链路照跑、断言照做，只是不出声。若哪天发现门禁时又有提示音外放，先怀疑有涉及音频的用例没打桩。

新增涉及音频的用例时：monkeypatch `_play` / `play_system`，或换假后端/假播放器（`test_notification.py` 的 `_FakeEffect`、`test_media_viewer.py` 的 `_FakePlayer` 两个先例）。
