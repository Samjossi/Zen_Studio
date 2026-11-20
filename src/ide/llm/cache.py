"""接口级模型目录缓存：进程级、按接口实现名隔离、无 TTL。

底层 `list_models`/`list_efforts` 可能是 CLI 子进程调用（timeout=15s 级），
GUI 线程「广播 × 标签数」放大后成倍卡顿；模型目录变化以天计，进程内
一次拉取即可。`clear()` 是唯一失效口（`registry.refresh_models()` 委托
本模块），不引入文件监听或后台刷新。

缓存键恒为接口实现名，不得改成厂商名或模块名——同一厂商下多接口各自
持有模型表（D6 红线 1：不跨后台共享模型表）。

锁覆盖「查 + 拉 + 写」全程：防并发 miss 时重复起子进程（设置中心与
ModelBar 可能并发拉取）；锁内阻塞仅发生在每接口首次拉取。
"""
import threading
from collections.abc import Callable

#: 模型别名缓存：接口名 → 不可变元组（对外返回副本，防调用方改写缓存）
_models_cache: dict[str, tuple[str, ...]] = {}

#: 模型级强度档位缓存：与 _models_cache 同生命周期、同锁、同失效口
_efforts_cache: dict[str, dict[str, tuple[list[str], str | None]]] = {}

#: 共享原始目录载荷缓存：同一接口的模型/强度/显示名多路派生只拉一次
_raw_cache: dict[str, object] = {}

_cache_lock = threading.Lock()


def cached_list_models(
        name: str, fn: Callable[[], list[str]]) -> Callable[[], list[str]]:
    """把接口级 list_models 包成缓存版：命中返回副本，miss 拉取并回填。"""
    def wrapper() -> list[str]:
        with _cache_lock:
            cached = _models_cache.get(name)
            if cached is None:
                cached = tuple(fn())
                _models_cache[name] = cached
        return list(cached)
    return wrapper


def cached_list_efforts(
        name: str,
        fn: Callable[[], dict[str, tuple[list[str], str | None]]],
) -> Callable[[], dict[str, tuple[list[str], str | None]]]:
    """把接口级 list_efforts 包成缓存版（list_models 同款）。"""
    def wrapper() -> dict[str, tuple[list[str], str | None]]:
        with _cache_lock:
            cached = _efforts_cache.get(name)
            if cached is None:
                cached = fn()
                _efforts_cache[name] = cached
        return dict(cached)
    return wrapper


def cached_raw(name: str, fn: Callable[[], object]) -> Callable[[], object]:
    """把原始目录载荷拉取包成缓存版（同接口多路派生只拉一次）。"""
    def wrapper() -> object:
        with _cache_lock:
            cached = _raw_cache.get(name)
            if cached is None:
                cached = fn()
                _raw_cache[name] = cached
        return cached
    return wrapper


def clear(name: str | None = None) -> None:
    """清理三路缓存：name=None 清全部，否则只清该接口。

    唯一失效口的实现；调用方（注册表 `refresh_models`）不直接触碰缓存
    字典，保证模型列表、强度档位、原始载荷始终同点失效。
    """
    with _cache_lock:
        if name is None:
            _models_cache.clear()
            _efforts_cache.clear()
            _raw_cache.clear()
            return
        _models_cache.pop(name, None)
        _efforts_cache.pop(name, None)
        _raw_cache.pop(name, None)


__all__ = [
    "cached_list_models",
    "cached_list_efforts",
    "cached_raw",
    "clear",
]
