"""
ripgrep (rg) 二进制的发现、下载、缓存和执行模块。

核心原则：让 rg 去碰文件系统，Python 只处理结果。
"""

import asyncio
import contextlib
import os
import platform
import shutil
import sys


class RipgrepNotFoundError(Exception):
    """rg 不可用时抛出"""


class RipgrepError(Exception):
    """rg 执行失败时抛出"""

# 平台映射表：平台键 -> (rg 目标三元组, 归档格式)
PLATFORM_MAP = {
    "x64-win32": ("x86_64-pc-windows-msvc", "zip"),
    "arm64-win32": ("aarch64-pc-windows-msvc", "zip"),
    "x64-darwin": ("x86_64-apple-darwin", "tar.gz"),
    "arm64-darwin": ("aarch64-apple-darwin", "tar.gz"),
    "x64-linux": ("x86_64-unknown-linux-musl", "tar.gz"),
    "arm64-linux": ("aarch64-unknown-linux-gnu", "tar.gz"),
}


def get_platform_key() -> str:
    """
    获取当前平台的键名。

    Returns:
        平台键，如 "x64-win32"、"arm64-darwin"
    """
    machine = platform.machine().lower()
    plat = sys.platform

    # 标准化架构名称
    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        arch = machine

    return f"{arch}-{plat}"


def get_rg_binary_name(platform_key: str) -> str:
    """
    获取 rg 二进制文件名。

    Args:
        platform_key: 平台键

    Returns:
        二进制文件名，Windows 上为 "rg.exe"，其他为 "rg"
    """
    if platform_key.endswith("-win32"):
        return "rg.exe"
    return "rg"


def get_cache_dir() -> str:
    """
    获取 rg 缓存目录路径。

    Returns:
        缓存目录路径：~/.illusion/ripgrep/
    """
    from illusion.config.paths import get_config_dir

    return str(get_config_dir() / "ripgrep")


def find_rg_path() -> str:
    """
    查找 rg 二进制路径。

    优先级：
    1. 环境变量 ILLUSION_RIPGREP_PATH
    2. 本地缓存 ~/.illusion/ripgrep/rg
    3. 系统 PATH

    Returns:
        rg 二进制路径

    Raises:
        RipgrepNotFoundError: rg 不可用时
    """
    # 1. 检查环境变量
    env_path = os.environ.get("ILLUSION_RIPGREP_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    # 2. 检查本地缓存
    cache_dir = get_cache_dir()
    platform_key = get_platform_key()
    binary_name = get_rg_binary_name(platform_key)
    cache_path = os.path.join(cache_dir, binary_name)
    if os.path.exists(cache_path):
        return cache_path

    # 3. 检查系统 PATH
    system_path = shutil.which("rg")
    if system_path:
        return system_path

    # 4. 以上均不可用
    raise RipgrepNotFoundError(
        "ripgrep (rg) 不可用。请手动安装 rg 或设置环境变量 ILLUSION_RIPGREP_PATH。"
        "下载地址：https://github.com/BurntSushi/ripgrep/releases"
    )


# GitHub Releases 下载地址模板
RG_DOWNLOAD_URL = "https://github.com/BurntSushi/ripgrep/releases/download/14.1.1/{archive}"


def extract_zip(zip_path: str, extract_dir: str) -> None:
    """
    解压 ZIP 文件到指定目录。

    Args:
        zip_path: ZIP 文件路径
        extract_dir: 解压目标目录
    """
    import zipfile
    with zipfile.ZipFile(zip_path, "r") as zf:
        # 查找 rg.exe 文件
        for name in zf.namelist():
            if name.endswith("rg.exe"):
                # 提取到目标目录
                zf.extract(name, extract_dir)
                # 移动到目标目录根目录
                extracted = os.path.join(extract_dir, name)
                target = os.path.join(extract_dir, "rg.exe")
                if extracted != target:
                    os.rename(extracted, target)
                break


def extract_tar(tar_path: str, extract_dir: str) -> None:
    """
    解压 TAR.GZ 文件到指定目录。

    Args:
        tar_path: TAR.GZ 文件路径
        extract_dir: 解压目标目录
    """
    import tarfile
    with tarfile.open(tar_path, "r:gz") as tf:
        # 查找 rg 文件
        for member in tf.getmembers():
            if member.name.endswith("/rg") or member.name == "rg":
                # 提取到目标目录；3.12+ 需显式过滤以消除
                # "Python 3.14 will filter extracted tar archives" 弃用警告
                if sys.version_info >= (3, 12):
                    tf.extract(member, extract_dir, filter="data")
                else:
                    tf.extract(member, extract_dir)
                # 移动到目标目录根目录
                extracted = os.path.join(extract_dir, member.name)
                target = os.path.join(extract_dir, "rg")
                if extracted != target:
                    os.rename(extracted, target)
                break


def download_rg() -> str:
    """
    下载 rg 二进制到缓存目录。

    Returns:
        下载后的 rg 二进制路径

    Raises:
        RipgrepNotFoundError: 下载失败时
    """
    import tempfile
    import urllib.request

    platform_key = get_platform_key()
    if platform_key not in PLATFORM_MAP:
        raise RipgrepNotFoundError(f"不支持的平台: {platform_key}")

    target_triple, archive_format = PLATFORM_MAP[platform_key]
    binary_name = get_rg_binary_name(platform_key)
    cache_dir = get_cache_dir()

    # 创建缓存目录
    os.makedirs(cache_dir, exist_ok=True)

    # 构建下载 URL
    if archive_format == "zip":
        archive_name = f"ripgrep-14.1.1-{target_triple}.zip"
    else:
        archive_name = f"ripgrep-14.1.1-{target_triple}.tar.gz"
    url = RG_DOWNLOAD_URL.format(archive=archive_name)

    # 下载文件
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{archive_format}") as tmp:
            tmp_path = tmp.name
            urllib.request.urlretrieve(url, tmp_path)
    except OSError as e:
        raise RipgrepNotFoundError(f"下载 rg 失败: {e}")

    try:
        # 解压文件
        if archive_format == "zip":
            extract_zip(tmp_path, cache_dir)
        else:
            extract_tar(tmp_path, cache_dir)

        # 设置权限（非 Windows）
        rg_path = os.path.join(cache_dir, binary_name)
        if sys.platform != "win32":
            os.chmod(rg_path, 0o755)

        return rg_path
    finally:
        # 清理临时文件
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


async def ensure_ripgrep() -> str:
    """
    确保 rg 可用，返回二进制路径。

    优先级：env > cache > PATH > download

    Returns:
        rg 二进制路径

    Raises:
        RipgrepNotFoundError: rg 不可用时
    """
    try:
        # 委托给线程池避免阻塞事件循环（find_rg_path 会做多次 os.path.exists / shutil.which）
        return await asyncio.to_thread(find_rg_path)
    except RipgrepNotFoundError:
        # 尝试下载（涉及网络与磁盘解压，必须放线程池中执行）
        return await asyncio.to_thread(download_rg)


async def run_rg(args: list[str], cwd: str | None = None,
                 timeout: float = 20.0) -> tuple[str, str, int]:
    """
    执行 rg 命令。

    Args:
        args: rg 命令行参数
        cwd: 工作目录
        timeout: 超时时间（秒）

    Returns:
        (stdout, stderr, returncode) 元组

    Raises:
        RipgrepError: 执行失败或超时时
    """
    import subprocess

    rg_path = await ensure_ripgrep()

    # 构建完整命令
    cmd = [rg_path] + args

    try:
        # 创建子进程
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,  # 防止 Windows 上的句柄继承死锁
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            # Windows 特殊处理
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        # 等待完成，带超时
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except TimeoutError:
            # 双重终止机制：先优雅终止，再强制杀死
            try:
                process.terminate()
            except (ProcessLookupError, OSError):
                pass  # 进程已退出
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except (TimeoutError, ProcessLookupError, OSError):
                # 优雅终止超时或失败，强制杀死
                try:
                    process.kill()
                except (ProcessLookupError, OSError):
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except (TimeoutError, ProcessLookupError, OSError):
                    pass  # 最终兜底，放弃等待
            raise RipgrepError(f"rg 执行超时（{timeout}秒）")
        except asyncio.CancelledError:
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass
            with contextlib.suppress(Exception):
                await process.wait()
            raise

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return stdout, stderr, process.returncode if process.returncode is not None else 1

    except RipgrepError:
        raise
    except OSError as e:
        raise RipgrepError(f"rg 执行失败: {e}")


async def run_rg_checked(
    args: list[str], cwd: str | None = None, timeout: float = 20.0
) -> tuple[str | None, str | None]:
    """运行 rg 并返回 (stdout, error_msg)，不抛出异常。

    将 :func:`run_rg` 的结果封装为不抛异常的接口，供工具层直接使用，
    避免 RipgrepError 在单工具路径下逃逸 _safe_run 导致后端崩溃。

    Args:
        args: rg 命令行参数
        cwd: 工作目录
        timeout: 超时时间（秒）

    Returns:
        (stdout, None): rg 成功执行（stdout 可能为空字符串表示无匹配）
        (None, error_msg): rg 执行失败、超时或返回非零退出码，
            error_msg 为人类可读的错误信息
    """
    try:
        stdout, stderr, returncode = await run_rg(args, cwd=cwd, timeout=timeout)
    except RipgrepError as exc:
        return None, str(exc)
    except RipgrepNotFoundError as exc:
        return None, str(exc)
    except (OSError, ValueError) as exc:
        return None, f"rg 执行失败: {exc}"

    # 退出码 1 表示无匹配，返回空 stdout（调用方自行处理）
    if returncode == 1:
        return "", None
    # 其他非零退出码表示错误
    if returncode != 0:
        return None, f"rg 执行失败（退出码 {returncode}）: {stderr}"

    return stdout, None
