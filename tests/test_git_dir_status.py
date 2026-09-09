"""GitStatusService 目录聚合（_dir_status / status_of_dir）单元测试
（收编自 scripts/test_git_dir_status.py）。

覆盖计划 2026-0725-0933 §8 验证清单的服务层场景。
不依赖真实 git/仓库：直接向 service 注入 _status 后调用 _build_dir_status()；
status_of_dir 的 _rel 换算用虚构仓库根（Path.resolve 非严格模式，无需落盘）。
"""
from __future__ import annotations

from core.git import status as st
from core.git.service import GitStatusService

REPO = "/repo"  # 虚构仓库根（不落盘）


def make_service(
    status_map: dict[str, str],
    collapsed_keys: tuple[str, ...] = (),
) -> GitStatusService:
    """注入 _status 后重建派生缓存；collapsed_keys 模拟 ls-files 来源
    （整体被忽略的目录键可不存在于 _status 中——ls-files 独立查询）。"""
    svc = GitStatusService(REPO)
    svc._repo_root = REPO
    svc._status = status_map
    svc._dir_status = svc._build_dir_status()
    svc._collapsed_keys = tuple(
        dict.fromkeys(
            (*collapsed_keys, *(k for k in svc._status if k.endswith("/")))
        )
    )
    return svc


def test_nested_untracked_bubbles_up():
    """三层嵌套新增 → 三级祖先均为 untracked，仓库根不入缓存。"""
    svc = make_service({"a/b/c/new.py": st.UNTRACKED})
    assert svc._dir_status.get("a/b/c") == st.UNTRACKED, "三层嵌套：a/b/c → untracked"
    assert svc._dir_status.get("a/b") == st.UNTRACKED, "三层嵌套：a/b → untracked"
    assert svc._dir_status.get("a") == st.UNTRACKED, "三层嵌套：a → untracked"
    assert "." not in svc._dir_status, "仓库根不入缓存"
    assert svc.status_of_dir(f"{REPO}/a/b") == st.UNTRACKED, "status_of_dir 绝对路径换算"
    assert svc.status_of_dir(REPO) is None, "status_of_dir 仓库根本身 → None"


def test_mixed_untracked_modified_merges_to_modified():
    """同树混合新增 + 修改 → 目录归并为 modified（显式优先级，不依赖遍历顺序）。"""
    for order in (
        {"d/x.py": st.UNTRACKED, "d/y.py": st.MODIFIED},
        {"d/y.py": st.MODIFIED, "d/x.py": st.UNTRACKED},
    ):
        svc = make_service(order)
        assert svc._dir_status.get("d") == st.MODIFIED, f"混合归并（{list(order.values())}）→ modified"


def test_deleted_does_not_bubble():
    svc = make_service({"e/gone.py": st.DELETED})
    assert "e" not in svc._dir_status, "deleted 不冒泡：e 无键"
    assert svc.status_of_dir(f"{REPO}/e") is None, "deleted 不冒泡：status_of_dir → None"


def test_deleted_with_untracked_takes_untracked():
    svc = make_service({"f/gone.py": st.DELETED, "f/new.py": st.UNTRACKED})
    assert svc._dir_status.get("f") == st.UNTRACKED, "deleted+untracked → untracked"


def test_ignored_collapsed_key_no_bubble():
    """ignored 折叠键 `dir/`：自身入缓存（去尾斜杠），但不向祖先冒泡
    （对齐 VS Code：ignored 仅自身暗显——2026-0730-1940 修复）。"""
    svc = make_service({"g/build/": st.IGNORED})
    assert svc._dir_status.get("g/build") == st.IGNORED, "ignored 折叠键自身 → ignored"
    assert "g" not in svc._dir_status, "ignored 不冒泡：g 无键"


def test_ignored_does_not_override_modified():
    """ignored 不盖过 modified（同一祖先下；ignored 缺席不干扰 modified 冒泡）。"""
    svc = make_service({"h/build/": st.IGNORED, "h/src/m.py": st.MODIFIED})
    assert svc._dir_status.get("h") == st.MODIFIED, "ignored 不盖过 modified"
    assert svc._dir_status.get("h/build") == st.IGNORED, "ignored 目录自身仍 ignored"


def test_non_collapsed_ignored_file_no_bubble():
    """非折叠 ignored 文件：不冒泡，自身状态经 status_of() 直查保留。"""
    svc = make_service({"j/cache/x.pyc": st.IGNORED})
    assert "j/cache" not in svc._dir_status, "非折叠 ignored 不冒泡：j/cache 无键"
    assert "j" not in svc._dir_status, "非折叠 ignored 不冒泡：j 无键"
    assert svc.status_of(f"{REPO}/j/cache/x.pyc") == st.IGNORED, "非折叠 ignored 文件自身状态保留"


def test_ignored_collapsed_key_propagates_down():
    """ignored 折叠键下透：子孙目录继承暗显（任意深度），兄弟前缀不误伤。"""
    svc = make_service({"g/build/": st.IGNORED})
    assert svc.status_of_dir(f"{REPO}/g/build/sub") == st.IGNORED, "下透：g/build/sub → ignored"
    assert svc.status_of_dir(f"{REPO}/g/build/sub/deep") == st.IGNORED, "下透：g/build/sub/deep → ignored"
    assert svc.status_of_dir(f"{REPO}/g/build2") is None, "下透不误伤兄弟前缀：g/build2 → None"
    assert "g" not in svc._dir_status, "下透不反向冒泡：g 仍无键"
    assert svc.status_of(f"{REPO}/g/build/sub/x.py") == st.IGNORED, "下透不改变文件链路：g/build/sub/x.py → ignored"


def test_ls_files_source_keys():
    """ls-files 来源键（_status 中无条目——`--untracked-files=all` 逐条
    展开 ignored 的真实形态）：ignored 目录自身与子孙同灰，散文件
    不误伤父目录（对齐 VS Code：单文件被忽略父目录不暗显）。"""
    svc = make_service(
        {"k/.venv/lib/python3/x.py": st.IGNORED, "k/cache/x.pyc": st.IGNORED},
        collapsed_keys=("k/.venv/",),
    )
    assert svc.status_of_dir(f"{REPO}/k/.venv") == st.IGNORED, "ls-files 键：ignored 目录自身 → ignored"
    assert svc.status_of_dir(f"{REPO}/k/.venv/lib") == st.IGNORED, "ls-files 键：子孙目录 → ignored"
    assert svc.status_of_dir(f"{REPO}/k/.venv/lib/python3") == st.IGNORED, "ls-files 键：任意深度子孙 → ignored"
    assert svc.status_of_dir(f"{REPO}/k/.venv2") is None, "ls-files 键不误伤兄弟前缀：k/.venv2 → None"
    assert svc.status_of_dir(f"{REPO}/k/cache") is None, "散 ignored 文件不暗显父目录：k/cache → None"
    assert svc.status_of_dir(f"{REPO}/k") is None, "散 ignored 文件不暗显父目录：k → None"
    assert svc.status_of(f"{REPO}/k/.venv/lib/python3/x.py") == st.IGNORED, "ls-files 键下透不影响文件直查：x.py → ignored"


def test_conflict_highest_priority():
    """conflict 最高优先级：深嵌套 conflict 盖过浅层 modified。"""
    svc = make_service({"i/j/k/c.py": st.CONFLICT, "i/m.py": st.MODIFIED})
    assert svc._dir_status.get("i/j/k") == st.CONFLICT, "conflict 盖过 modified（i/j/k）"
    assert svc._dir_status.get("i/j") == st.CONFLICT, "conflict 盖过 modified（i/j）"
    assert svc._dir_status.get("i") == st.CONFLICT, "conflict 盖过 modified（i）"


def test_no_changes_empty_cache():
    svc = make_service({})
    assert svc._dir_status == {}, "无变更 → 空缓存"


def test_outside_repo_returns_none():
    svc = make_service({"a/x.py": st.MODIFIED})
    assert svc.status_of_dir("/elsewhere/a") is None, "仓库外路径 → None"
