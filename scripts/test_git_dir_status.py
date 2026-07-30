"""GitStatusService 目录聚合（_dir_status / status_of_dir）单元测试。

覆盖计划 2026-0725-0933 §8 验证清单的服务层场景：
    uv run python scripts/test_git_dir_status.py

不依赖真实 git/仓库：直接向 service 注入 _status 后调用 _build_dir_status()；
status_of_dir 的 _rel 换算用虚构仓库根（Path.resolve 非严格模式，无需落盘）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.git import status as st
from core.git.service import GitStatusService

REPO = "/repo"  # 虚构仓库根（不落盘）


def make_service(status_map: dict[str, str]) -> GitStatusService:
    svc = GitStatusService(REPO)
    svc._repo_root = REPO
    svc._status = status_map
    svc._dir_status = svc._build_dir_status()
    return svc


def check(name: str, cond: bool) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        raise SystemExit(f"断言失败：{name}")


# 1. 三层嵌套新增 → 三级祖先均为 untracked，仓库根不入缓存
svc = make_service({"a/b/c/new.py": st.UNTRACKED})
check("三层嵌套：a/b/c → untracked", svc._dir_status.get("a/b/c") == st.UNTRACKED)
check("三层嵌套：a/b → untracked", svc._dir_status.get("a/b") == st.UNTRACKED)
check("三层嵌套：a → untracked", svc._dir_status.get("a") == st.UNTRACKED)
check("仓库根不入缓存", "." not in svc._dir_status)
check("status_of_dir 绝对路径换算", svc.status_of_dir(f"{REPO}/a/b") == st.UNTRACKED)
check("status_of_dir 仓库根本身 → None", svc.status_of_dir(REPO) is None)

# 2. 同树混合新增 + 修改 → 目录归并为 modified（显式优先级，不依赖遍历顺序）
for order in (
    {"d/x.py": st.UNTRACKED, "d/y.py": st.MODIFIED},
    {"d/y.py": st.MODIFIED, "d/x.py": st.UNTRACKED},
):
    svc = make_service(order)
    check(f"混合归并（{list(order.values())}）→ modified", svc._dir_status.get("d") == st.MODIFIED)

# 3. 仅含 deleted → 不冒泡
svc = make_service({"e/gone.py": st.DELETED})
check("deleted 不冒泡：e 无键", "e" not in svc._dir_status)
check("deleted 不冒泡：status_of_dir → None", svc.status_of_dir(f"{REPO}/e") is None)

# 4. deleted 与 untracked 共存 → 目录取 untracked
svc = make_service({"f/gone.py": st.DELETED, "f/new.py": st.UNTRACKED})
check("deleted+untracked → untracked", svc._dir_status.get("f") == st.UNTRACKED)

# 5. ignored 折叠键 `dir/`：自身入缓存（去尾斜杠），但不向祖先冒泡
#    （对齐 VS Code：ignored 仅自身暗显——2026-0730-1940 修复）
svc = make_service({"g/build/": st.IGNORED})
check("ignored 折叠键自身 → ignored", svc._dir_status.get("g/build") == st.IGNORED)
check("ignored 不冒泡：g 无键", "g" not in svc._dir_status)

# 6. ignored 不盖过 modified（同一祖先下；ignored 缺席不干扰 modified 冒泡）
svc = make_service({"h/build/": st.IGNORED, "h/src/m.py": st.MODIFIED})
check("ignored 不盖过 modified", svc._dir_status.get("h") == st.MODIFIED)
check("ignored 目录自身仍 ignored", svc._dir_status.get("h/build") == st.IGNORED)

# 6b. 非折叠 ignored 文件：不冒泡，自身状态经 status_of() 直查保留
svc = make_service({"j/cache/x.pyc": st.IGNORED})
check("非折叠 ignored 不冒泡：j/cache 无键", "j/cache" not in svc._dir_status)
check("非折叠 ignored 不冒泡：j 无键", "j" not in svc._dir_status)
check("非折叠 ignored 文件自身状态保留",
      svc.status_of(f"{REPO}/j/cache/x.pyc") == st.IGNORED)

# 7. conflict 最高优先级：深嵌套 conflict 盖过浅层 modified
svc = make_service({"i/j/k/c.py": st.CONFLICT, "i/m.py": st.MODIFIED})
check("conflict 盖过 modified（i/j/k）", svc._dir_status.get("i/j/k") == st.CONFLICT)
check("conflict 盖过 modified（i/j）", svc._dir_status.get("i/j") == st.CONFLICT)
check("conflict 盖过 modified（i）", svc._dir_status.get("i") == st.CONFLICT)

# 8. 无变更 → 空缓存
svc = make_service({})
check("无变更 → 空缓存", svc._dir_status == {})

# 9. 仓库外路径 → None
svc = make_service({"a/x.py": st.MODIFIED})
check("仓库外路径 → None", svc.status_of_dir("/elsewhere/a") is None)

print("\n全部断言通过。")
