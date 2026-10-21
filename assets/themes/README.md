# assets/themes/ — QSS 主题模板

全局唯一的 QSS 模板 `base.qss` 的存放目录，是 `src/gui/theme.py` 消费的**只读资源**，随打包收编进产物。

## 机制

- 全部主题共用**唯一模板** `base.qss`：色值以 `$token` 占位符书写，运行时由 `string.Template` 按 `theme.py` 的 `THEME_PALETTES[主题名]` 渲染（缺键即抛 `KeyError`，fail-fast）。
- 耦合色（如菜单/下拉同底色）在模板中物理同源，各主题间不可能漂移；**新增主题 = 在 `THEME_PALETTES` 加一行字典**，不新增 qss 文件。
- 语法高亮/终端 ANSI 等色值令牌不在本目录，全部汇聚在 `theme.py` 模块级常量中。

## 纪律

- `base.qss` 是**只读资源**（非用户数据），**禁止**误置到 `config/` 下——曾因此导致打包态静默丢失全部样式。
- 本目录随 `building/zen-studio.spec` 收编；改动模板后提交前必跑本地门禁 `bash scripts/check.sh`。
- **禁止**为主题新建第二份 qss 副本——模板化纪律即"单模板 + 令牌渲染"。
