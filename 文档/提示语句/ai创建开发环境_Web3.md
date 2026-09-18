# ai创建开发环境_Web3

> **用途**：将本文档交给 AI，即可在新项目中复现一套完整的 Solidity + Python 开发测试环境
> **环境构成**：Foundry（Anvil 本地链）+ uv 虚拟环境 + web3.py / pytest / py-solc-x + solc 编译器
> **适用场景**：Solidity 合约开发，Python（web3.py + pytest）自动化测试

---

## 0. 前置约束

- 强制使用项目 `.venv` 的 Python，禁止系统全局 Python
- 临时文件、测试脚本放项目根 `./.temp/`，禁止系统临时目录
- Foundry 为用户级工具链安装（`~/.foundry/`），与 rustup/pyenv 同类，属正常例外
- solc 不装全局；由 `py-solc-x` 管理，二进制缓存在用户级 `~/.solcx/`，按项目锁版本

---

## 1. 安装 Foundry（先检查，缺失才装）

```bash
# 检查是否已安装
which anvil || curl -L https://foundry.paradigm.xyz | bash

# 安装/更新工具链
export PATH="$PATH:$HOME/.foundry/bin"
foundryup
```

```bash
# PATH 持久化（已存在则跳过）
grep -q '.foundry/bin' ~/.bashrc || echo 'export PATH="$PATH:$HOME/.foundry/bin"' >> ~/.bashrc
```

**验证**：`anvil --version` 输出版本号。

---

## 2. 创建虚拟环境

```bash
# 使用 Python 3.12 创建（必须先 cd 进入项目目录）
uv venv .venv --python 3.12
source .venv/bin/activate

# 初始化项目（生成 pyproject.toml；已有则跳过）
uv init --bare
```

**验证**：`.venv/bin/python --version` 输出 3.12.x。

---

## 3. 安装依赖

```bash
uv add web3 pytest py-solc-x eth-utils
```

| 包 | 用途 |
|:---|:---|
| `web3` | 与 Anvil / 测试网交互 |
| `pytest` | 测试框架 |
| `py-solc-x` | solc 版本管理 + 编译驱动 |
| `eth-utils` | 地址、单位换算等工具 |

**验证**：`.venv/bin/python -c "import web3, pytest, solcx"` 无报错。

---

## 4. 预装 solc 编译器

```bash
# 版本按项目需要调整
.venv/bin/python -c "import solcx; solcx.install_solc('0.8.26')"
```

**验证**：`.venv/bin/python -c "import solcx; print(solcx.get_installed_solc_versions())"` 包含所装版本。

---

## 5. 创建工作目录

按「一个仓库一个项目」原则，平铺在根目录：

```bash
mkdir -p contracts tests scripts
```

| 目录 | 内容 |
|:---|:---|
| `contracts/` | Solidity 合约源码 |
| `tests/` | pytest 测试（`test_*.py`、`conftest.py`） |
| `scripts/` | 编译、部署辅助脚本 |

---

## 6. 补充 .gitignore

```gitignore
.venv/
.temp/
__pycache__/
.pytest_cache/
```

---

## 7. 端到端冒烟测试

写最小脚本到 `./.temp/smoke_test.py`：用 `py-solc-x` 编译一个最小合约，后台启动 `anvil`，`web3.py` 连接（`http://127.0.0.1:8545`）部署并调用一个 view 函数。断言返回值正确后，关闭 Anvil 并删除脚本。

**通过标准**：合约部署成功、view 函数返回值正确——全链路打通，环境就绪。

---

## 8. 常见故障

| 故障 | 处理 |
|:---|:---|
| `foundry.paradigm.xyz` 下载慢 | 换 GitHub Release 手动下载 foundryup |
| `py-solc-x` 下载 solc 失败 | solc 二进制在 GitHub Release，手动下载放入 `~/.solcx/` |
| `uv` 未安装 | `curl -LsSf https://astral.sh/uv/install.sh \| sh`（用户级） |
| `foundryup` 不在 PATH | `export PATH="$PATH:$HOME/.foundry/bin"` 后重试 |

---

*基于项目一环境准备实践整理：2026-09-09 (UTC+8)*
