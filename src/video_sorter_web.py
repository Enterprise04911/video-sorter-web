#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VideoSorter Web —— 视频自动分类管理工具（Web 版）

特性
  · 纯 Python 标准库，无需 pip 安装任何依赖
  · 内置单页 Web 界面（浏览器操作）
  · AI 模块同时适配 DeepSeek / 通义千问 / 智谱 / 硅基流动 / Kimi / OpenAI / Ollama / 任意兼容接口
  · 根据视频文件名关键字（创作者名）分类到目标库子文件夹
  · 自由选择源文件夹与目标库文件夹
  · 监控模式：源目录有新文件落地自动分类
  · AI 关闭时：本地检索文件名关键字是否命中目标库已有子文件夹，命中则直接迁入
  · 未命中且有 AI 时交给 AI；无 AI 时用正则从文件名识别人名新建文件夹
  · 支持演练模式（dry-run）、复制模式、递归扫描
  · 分类需求留言板：用户可自定义 AI 分类补充规则，持久化到 config.json 并注入 AI 提示词

用法
  python video_sorter_web.py                              # 启动 Web 界面
  python video_sorter_web.py --port 9000                  # 指定端口
  python video_sorter_web.py --no-browser                 # 不自动开浏览器
  python video_sorter_web.py --cli -s <源> -t <目标库>      # 命令行扫描一次
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError


# ======================================================================
#  常量
# ======================================================================

APP_NAME = "VideoSorter Web"
CONFIG_DIR = Path.home() / ".video_sorter"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "videosorter.log"

VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".ts", ".m2ts", ".mpg", ".mpeg", ".rmvb", ".rm", ".3gp", ".vob", ".f4v",
}

# -------------------- AI 厂商预设 --------------------
AI_PRESETS = {
    "deepseek": {
        "label": "DeepSeek（推荐）",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "note": "platform.deepseek.com 申请 Key，价格便宜",
    },
    "qwen": {
        "label": "通义千问 Qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-turbo",
        "models": ["qwen-turbo", "qwen-plus", "qwen-max", "qwen2.5-7b-instruct"],
        "note": "dashscope.console.aliyun.com 申请，qwen-turbo 便宜",
    },
    "zhipu": {
        "label": "智谱 GLM（glm-4-flash 免费）",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "models": ["glm-4-flash", "glm-4-air", "glm-4-plus"],
        "note": "open.bigmodel.cn 申请，flash 系列免费",
    },
    "siliconflow": {
        "label": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "models": [
            "Qwen/Qwen2.5-7B-Instruct",
            "Qwen/Qwen2.5-72B-Instruct",
            "deepseek-ai/DeepSeek-V3",
            "THUDM/glm-4-9b-chat",
        ],
        "note": "siliconflow.cn 申请，部分模型免费",
    },
    "moonshot": {
        "label": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k"],
        "note": "platform.moonshot.cn 申请",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"],
        "note": "海外服务，需科学上网",
    },
    "ollama": {
        "label": "本地 Ollama（完全免费）",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:7b",
        "models": ["qwen2.5:7b", "qwen2.5:14b", "llama3.1:8b", "deepseek-r1:7b"],
        "note": "本地运行 ollama serve，无需 API Key",
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）",
        "base_url": "",
        "model": "",
        "models": [],
        "note": "任何实现了 /chat/completions 的服务",
    },
}

DEFAULT_CONFIG = {
    "source_dir": "",
    "target_dir": "",
    "recursive": False,

    "dry_run": False,
    "move_mode": "move",
    "create_new_folder": True,
    "fallback_folder": "未分类",

    "ai_enabled": False,
    "ai_provider": "deepseek",
    "ai_base_url": AI_PRESETS["deepseek"]["base_url"],
    "ai_api_key": "",
    "ai_model": AI_PRESETS["deepseek"]["model"],
    "ai_timeout": 30,
    "ai_notes": "",          # 👈 分类需求留言板（用户自定义补充规则）

    "monitor_interval": 3,
    "web_host": "127.0.0.1",
    "web_port": 8765,
}


def load_config() -> dict:
    """读取配置。若文件不存在/损坏，回退到默认值，不会被覆盖崩溃。"""
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update(data)
        except Exception as e:
            print(f"[警告] 读取配置失败：{e}")
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[警告] 保存配置失败：{e}")


# ======================================================================
#  工具函数
# ======================================================================

_SEP_CHARS = " \t\r\n_-–—|·・,，。.、:：;；!！?？+&'\"“”‘’()[]【】（）{}<>《》"
_SEP_RE = re.compile("[" + re.escape(_SEP_CHARS) + "]+")

_QUALITY_RE = re.compile(
    r"(?i)(2160p|1080p|720p|480p|4k|8k|uhd|fhd|\bhd\b|hdr|dolby|hevc|h\.?264|h\.?265|"
    r"x264|x265|avc|aac|dts|web[-\s]?dl|webrip|bluray|bdrip|hdtv|60fps|30fps|"
    r"完整版|高清|超清|蓝光|中字|字幕|修复版|重制版)"
)
_BRACKET_RE = re.compile(r"[\[【（(]([^\]】）)]{1,40})[\]】）)]")


def normalize(s: str) -> str:
    return _SEP_RE.sub("", s or "").lower()


def strip_noise(s: str) -> str:
    s = _QUALITY_RE.sub(" ", s or "")
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" -_.").strip()


def sanitize_folder_name(name: str) -> str:
    name = strip_noise(name).strip("[]【】()（）{}<>《》 \t")
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = DEFAULT_CONFIG["fallback_folder"]
    return name[:60]


def is_plausible_name(s: str) -> bool:
    if not s:
        return False
    t = strip_noise(s).strip("[]【】()（）{}<>《》 \t")
    if len(t) < 2 or len(t) > 40:
        return False
    if not re.search(r"[A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", t):
        return False
    if re.fullmatch(r"[\d\W_]+", t):
        return False
    return True


def guess_creator(stem: str):
    m = re.match(r"^\s*[\[【（(]\s*([^\]】）)]{1,40}?)\s*[\]】）)]", stem)
    if m and is_plausible_name(m.group(1)):
        return sanitize_folder_name(m.group(1))
    m = re.search(r"@\s*([^\s@#]{1,40})\s*$", stem)
    if m and is_plausible_name(m.group(1)):
        return sanitize_folder_name(m.group(1))
    m = re.match(r"^\s*(.{1,40}?)\s*[-–—_|:：]\s*\S", stem)
    if m and is_plausible_name(m.group(1)):
        return sanitize_folder_name(m.group(1))
    for tag in _BRACKET_RE.findall(stem):
        if is_plausible_name(tag):
            return sanitize_folder_name(tag)
    return None


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    i = 1
    while True:
        cand = p.with_name(f"{p.stem}_{i}{p.suffix}")
        if not cand.exists():
            return cand
        i += 1


# ======================================================================
#  AI Provider（OpenAI 兼容）
# ======================================================================

class AIError(RuntimeError):
    pass


class OpenAICompatProvider:
    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: float = 30, ai_notes: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or "gpt-4o-mini"
        self.timeout = float(timeout or 30)
        # 👈 分类需求留言板：逐字保留，分类时注入提示词
        self.ai_notes = (ai_notes or "").strip()

    # -------- 底层调用 --------
    def _chat(self, messages: list, max_tokens: int = 64) -> str:
        if not self.base_url:
            raise AIError("未配置 AI 接口地址")

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        req = urlrequest.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:240]
            raise AIError(f"HTTP {e.code}: {body}") from e
        except URLError as e:
            raise AIError(f"网络错误：{e.reason}") from e
        except Exception as e:
            raise AIError(f"请求失败：{e}") from e

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise AIError(f"响应不是合法 JSON：{raw[:160]}") from e

        if isinstance(data, dict) and "choices" in data:
            return (data["choices"][0]["message"]["content"] or "").strip()
        if isinstance(data, dict) and "message" in data:
            return (data["message"]["content"] or "").strip()
        if isinstance(data, dict) and "response" in data:
            return (data["response"] or "").strip()
        raise AIError(f"无法解析的响应结构：{raw[:160]}")

    # -------- 连通性测试 --------
    def ping(self) -> str:
        return self._chat(
            [{"role": "user", "content": "只回复两个字：OK"}], max_tokens=8
        )

    # -------- 分类 --------
    def classify(self, filename: str, folders: list) -> str:
        folder_lines = "\n".join(f"- {f}" for f in folders[:200]) or "（当前没有任何分类文件夹）"

        # 👇 注入用户自定义分类需求（若有）
        notes_block = ""
        if self.ai_notes:
            notes_block = (
                "\n【用户补充的分类需求｜优先级高于默认规则，必须严格遵守】：\n"
                f"{self.ai_notes}\n"
            )

        user_prompt = (
            "你要把视频文件按【创作者 / 作者】归档到文件夹中。\n"
            f"{notes_block}\n"
            f"视频文件名：{filename}\n\n"
            f"目标库中已有的文件夹（每个代表一位创作者）：\n{folder_lines}\n\n"
            "请判断这个视频应放到哪个文件夹。只输出一行，严格二选一：\n"
            "1) 属于某个已有文件夹 → 直接输出该文件夹完整名称（原样，不加引号）；\n"
            "2) 属于新创作者 → 输出 NEW:创作者名称\n"
            "不要输出解释、序号、标点或任何多余文字。"
        )
        return self._chat(
            [
                {"role": "system", "content": "你是视频文件分类助手，只输出一行结果。"},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=48,
        )


def parse_ai_result(text: str, folders: list):
    if not text:
        return None, False
    line = text.strip().splitlines()[0].strip().strip("\"'“”‘’`。.、,， ")
    if not line:
        return None, False
    upper = line.upper()
    if upper.startswith("NEW:") or upper.startswith("NEW："):
        return (line[4:].strip().strip("\"'“”‘’` ") or None), True
    for f in folders:
        if f == line:
            return f, False
    nl = normalize(line)
    for f in folders:
        if normalize(f) == nl:
            return f, False
    for f in folders:
        nf = normalize(f)
        if nf and len(nf) >= 2 and (nf in nl or (nl and nl in nf)):
            return f, False
    if 1 <= len(line) <= 40:
        return line, True
    return None, False


# ======================================================================
#  核心分类器
# ======================================================================

class Sorter:
    def __init__(self, cfg: dict, log_func):
        self.cfg = dict(cfg)
        self.log = log_func
        self.stats = {"ok": 0, "fail": 0, "skip": 0}
        self._ai = None
        self._refresh_ai()

    def _refresh_ai(self):
        self._ai = None
        if self.cfg.get("ai_enabled"):
            self._ai = OpenAICompatProvider(
                self.cfg.get("ai_base_url", ""),
                self.cfg.get("ai_api_key", ""),
                self.cfg.get("ai_model", ""),
                self.cfg.get("ai_timeout", 30),
                self.cfg.get("ai_notes", ""),     # 👈 传入留言板内容
            )

    @property
    def source_dir(self):
        s = self.cfg.get("source_dir")
        return Path(s) if s else None

    @property
    def target_dir(self):
        t = self.cfg.get("target_dir")
        return Path(t) if t else None

    def list_target_folders(self):
        td = self.target_dir
        if not td or not td.is_dir():
            return []
        out = []
        try:
            for p in sorted(td.iterdir()):
                if p.is_dir() and not p.name.startswith("."):
                    out.append(p.name)
        except OSError as e:
            self.log(f"[警告] 读取目标库失败：{e}")
        return out

    @staticmethod
    def match_existing(stem: str, folders: list):
        n_stem = normalize(stem)
        best, best_len = None, 0
        for f in folders:
            nf = normalize(f)
            if len(nf) < 2:
                continue
            if nf in n_stem and len(nf) > best_len:
                best, best_len = f, len(nf)
        return best

    def decide(self, filename: str, folders=None):
        stem = Path(filename).stem
        if folders is None:
            folders = self.list_target_folders()

        # 1) AI 模式
        if self._ai is not None:
            try:
                raw = self._ai.classify(filename, folders)
                name, is_new = parse_ai_result(raw, folders)
                if name:
                    if is_new:
                        name = sanitize_folder_name(name)
                    tag = "新建" if is_new else "归入已有"
                    return name, is_new, f"AI 判定[{tag}]（{raw.strip()[:40]}）"
                self.log(f"[AI] 结果无法解析：{raw!r}，回退本地匹配")
            except AIError as e:
                self.log(f"[AI] 调用失败：{e}，回退本地匹配")
            except Exception as e:
                self.log(f"[AI] 未知异常：{e}，回退本地匹配")

        # 2) 本地关键字
        hit = self.match_existing(stem, folders)
        if hit:
            return hit, False, "本地关键字命中目标库已有文件夹"

        # 3) 自动建文件夹
        if self.cfg.get("create_new_folder", True):
            guess = guess_creator(stem)
            if guess:
                return guess, (guess not in folders), "从文件名识别人名，新建文件夹"
            fb = strip_noise(stem)[:40]
            if fb:
                fbn = sanitize_folder_name(fb)
                return fbn, (fbn not in folders), "无法识别人名，使用文件名兜底"

        # 4) 兜底
        fb = self.cfg.get("fallback_folder") or "未分类"
        return fb, (fb not in folders), "未能识别，归入兜底文件夹"

    def iter_videos(self):
        src = self.source_dir
        if not src or not src.is_dir():
            return []
        td = self.target_dir
        pattern = "**/*" if self.cfg.get("recursive") else "*"
        result = []
        try:
            for p in sorted(src.glob(pattern)):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in VIDEO_EXTS:
                    continue
                if td is not None:
                    try:
                        if str(p.resolve()).startswith(str(td.resolve())):
                            continue
                    except Exception:
                        pass
                result.append(p)
        except OSError as e:
            self.log(f"[警告] 遍历源目录失败：{e}")
        return result

    def process_file(self, path):
        path = Path(path)
        if not path.is_file():
            return None
        if path.suffix.lower() not in VIDEO_EXTS:
            self.stats["skip"] += 1
            return None

        try:
            folders = self.list_target_folders()
            folder, is_new, reason = self.decide(path.name, folders)
        except Exception as e:
            self.log(f"[错误] 判定失败 {path.name}：{e}")
            self.stats["fail"] += 1
            return None

        if not folder:
            self.log(f"[跳过] {path.name}：无法确定分类")
            self.stats["skip"] += 1
            return None

        td = self.target_dir
        if td is None:
            self.log("[错误] 未配置目标库")
            self.stats["fail"] += 1
            return None

        target_folder = td / folder
        verb = "复制" if self.cfg.get("move_mode") == "copy" else "移动"
        mark = "＋" if is_new else "→"

        if self.cfg.get("dry_run"):
            self.log(f"[演练] {path.name}  {mark}  {folder}/  （{reason}）")
            return None

        try:
            target_folder.mkdir(parents=True, exist_ok=True)
            dst = unique_path(target_folder / path.name)
            if self.cfg.get("move_mode") == "copy":
                shutil.copy2(str(path), str(dst))
            else:
                shutil.move(str(path), str(dst))
        except Exception as e:
            self.log(f"[错误] {verb}失败 {path.name}：{e}")
            self.stats["fail"] += 1
            return None

        self.stats["ok"] += 1
        prefix = "新建文件夹并" if is_new else ""
        self.log(f"[完成] {path.name}  {mark}  {folder}/  （{prefix}{verb}；{reason}）")
        return dst

    def scan_once(self):
        src = self.source_dir
        if not src or not src.is_dir():
            self.log("[错误] 源文件夹无效或不存在")
            return
        td = self.target_dir
        if td is None:
            self.log("[错误] 未配置目标库")
            return
        try:
            td.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.log(f"[错误] 无法创建目标库：{e}")
            return

        files = self.iter_videos()
        mode = "AI 判断" if self._ai else "本地关键字匹配"
        if self._ai is not None and self._ai.ai_notes:
            mode += "＋留言板规则"
        self.log(f"[扫描] 模式：{mode}｜源目录发现 {len(files)} 个视频文件")
        if not files:
            self.log("[扫描] 没有需要处理的文件")
            return
        for f in files:
            self.process_file(f)
        s = self.stats
        self.log(f"[统计] 成功 {s['ok']}｜失败 {s['fail']}｜跳过 {s['skip']}")


# ======================================================================
#  监控线程
# ======================================================================

class MonitorWorker(threading.Thread):
    def __init__(self, sorter: Sorter, interval: int, stop_event: threading.Event, log_func):
        super().__init__(daemon=True, name="VideoSorter-Monitor")
        self.sorter = sorter
        self.interval = max(1, int(interval or 3))
        self.stop_event = stop_event
        self.log = log_func
        self._seen = {}
        self._tries = {}

    def run(self):
        self.log(f"[监控] 已启动，扫描间隔 {self.interval}s，等待源目录新文件…")
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as e:
                self.log(f"[监控] 异常：{e}")
            self.stop_event.wait(self.interval)
        self.log("[监控] 已停止")

    def tick(self):
        src = self.sorter.source_dir
        if not src or not src.is_dir():
            return
        current = {}
        for p in self.sorter.iter_videos():
            try:
                st = p.stat()
            except OSError:
                continue
            current[str(p)] = (st.st_size, st.st_mtime)

        ready = []
        for k, sig in current.items():
            prev = self._seen.get(k)
            if prev is None:
                self._seen[k] = (sig, 0)
            elif prev[0] == sig:
                if prev[1] >= 1:
                    ready.append(k)
                else:
                    self._seen[k] = (sig, prev[1] + 1)
            else:
                self._seen[k] = (sig, 0)

        for k in list(self._seen):
            if k not in current:
                self._seen.pop(k, None)
                self._tries.pop(k, None)
        for k in list(self._tries):
            if k not in current:
                self._tries.pop(k, None)

        for k in ready:
            self._seen.pop(k, None)
            self._tries[k] = self._tries.get(k, 0) + 1
            if self._tries[k] > 3:
                continue
            self.sorter.process_file(Path(k))


# ======================================================================
#  校验 & 日志总线
# ======================================================================

def validate_config(cfg: dict):
    src = (cfg.get("source_dir") or "").strip()
    tgt = (cfg.get("target_dir") or "").strip()
    if not src:
        return "请先选择源文件夹"
    if not Path(src).is_dir():
        return f"源文件夹不存在：{src}"
    if not tgt:
        return "请先选择目标库文件夹"
    tgt_path = Path(tgt)
    if tgt_path.exists() and not tgt_path.is_dir():
        return f"目标库路径不是文件夹：{tgt}"
    try:
        s = Path(src).resolve()
        t = tgt_path.resolve()
    except OSError:
        return "路径解析失败"
    if s == t:
        return "源文件夹与目标库不能是同一个目录"
    if cfg.get("ai_enabled"):
        if not (cfg.get("ai_base_url") or "").strip():
            return "启用 AI 时必须填写接口地址"
        if not (cfg.get("ai_model") or "").strip():
            return "启用 AI 时必须填写模型名称"
    return None


class LogBus:
    def __init__(self, path: Path, capacity: int = 2000):
        self.path = path
        self.capacity = capacity
        self.buf = []
        self.seq = 0
        self.lock = threading.Lock()

    def write(self, msg: str):
        line = f"[{datetime.now():%H:%M:%S}] {msg}"
        with self.lock:
            self.seq += 1
            self.buf.append({"i": self.seq, "t": line})
            if len(self.buf) > self.capacity:
                del self.buf[: len(self.buf) - self.capacity]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def since(self, seq: int):
        with self.lock:
            return [x for x in self.buf if x["i"] > seq], self.seq


# ======================================================================
#  Web 应用状态
# ======================================================================

class WebApp:
    def __init__(self, cfg: dict):
        self.cfg = dict(cfg)
        self.logs = LogBus(LOG_FILE)
        self.lock = threading.Lock()
        self.busy = False
        self.monitor: MonitorWorker | None = None
        self.stop_event: threading.Event | None = None
        self.last_stats = {"ok": 0, "fail": 0, "skip": 0}
        self._pick_lock = threading.Lock()

    # -------- 状态 --------
    def state(self):
        return {
            "busy": self.busy,
            "monitor_running": bool(self.monitor and self.monitor.is_alive()),
            "stats": self.last_stats,
        }

    # -------- 扫描 --------
    def start_scan(self):
        with self.lock:
            if self.busy:
                return False, "任务正在运行中，请稍候"
            err = validate_config(self.cfg)
            if err:
                return False, err
            self.busy = True
        save_config(self.cfg)

        def _run():
            try:
                sorter = Sorter(self.cfg, self.logs.write)
                self.logs.write("=" * 60)
                self.logs.write("[任务] 开始扫描分类")
                sorter.scan_once()
                self.last_stats = dict(sorter.stats)
            except Exception as e:
                self.logs.write(f"[错误] 扫描异常：{e}")
            finally:
                with self.lock:
                    self.busy = False

        threading.Thread(target=_run, daemon=True).start()
        return True, "扫描已启动"

    # -------- 监控 --------
    def start_monitor(self):
        with self.lock:
            if self.monitor and self.monitor.is_alive():
                return False, "监控已经在运行"
            err = validate_config(self.cfg)
            if err:
                return False, err
        save_config(self.cfg)
        sorter = Sorter(self.cfg, self.logs.write)
        self.stop_event = threading.Event()
        self.monitor = MonitorWorker(
            sorter, self.cfg.get("monitor_interval", 3), self.stop_event, self.logs.write
        )
        self.monitor.start()
        if self.cfg.get("dry_run"):
            self.logs.write("[监控] 注意：当前为演练模式，只记录不会真正移动")
        return True, "监控已启动"

    def stop_monitor(self):
        if self.stop_event is not None:
            self.stop_event.set()
        self.monitor = None
        self.stop_event = None
        return True, "监控已停止"

    # -------- AI 测试 --------
    def test_ai(self):
        err = None
        if not (self.cfg.get("ai_base_url") or "").strip():
            err = "未填写 AI 接口地址"
        if not (self.cfg.get("ai_model") or "").strip():
            err = err or "未填写模型名称"
        if err:
            return False, err
        provider = OpenAICompatProvider(
            self.cfg.get("ai_base_url", ""),
            self.cfg.get("ai_api_key", ""),
            self.cfg.get("ai_model", ""),
            self.cfg.get("ai_timeout", 30),
            self.cfg.get("ai_notes", ""),   # 测试连接不使用，但保持构造一致
        )
        try:
            reply = provider.ping()
            return True, f"连接成功，模型回复：{reply[:60]}"
        except AIError as e:
            return False, f"连接失败：{e}"
        except Exception as e:
            return False, f"未知错误：{e}"

    # -------- 服务器端文件选择（本地运行时可用） --------
    def pick_folder(self, initial: str = ""):
        with self._pick_lock:
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                try:
                    root.attributes("-topmost", True)
                except Exception:
                    pass
                path = filedialog.askdirectory(
                    title="选择文件夹",
                    initialdir=initial or str(Path.home()),
                )
                root.destroy()
                return path or ""
            except Exception as e:
                self.logs.write(f"[提示] 服务器端文件对话框不可用（{e}），请手动输入路径")
                return ""


# ======================================================================
#  HTTP Handler
# ======================================================================

class Handler(BaseHTTPRequestHandler):
    server_version = "VideoSorterWeb/1.0"

    @property
    def app(self) -> WebApp:
        return self.server.app  # type: ignore[attr-defined]

    # -------- 工具 --------
    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _html(self, text: str):
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def log_message(self, *args, **kwargs):
        pass  # 屏蔽访问日志

    # -------- 路由 --------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path in ("/", "/index.html"):
            self._html(HTML_PAGE)
        elif path == "/api/bootstrap":
            self._json({
                "config": self.app.cfg,
                "presets": AI_PRESETS,
                "state": self.app.state(),
                "config_file": str(CONFIG_FILE),
            })
        elif path == "/api/state":
            self._json(self.app.state())
        elif path == "/api/logs":
            since = 0
            if "?since=" in self.path:
                try:
                    since = int(self.path.split("?since=", 1)[1].split("&")[0])
                except ValueError:
                    since = 0
            lines, seq = self.app.logs.since(since)
            self._json({"lines": lines, "seq": seq})
        else:
            self._json({"ok": False, "msg": "Not Found"}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        body = self._read_json()

        if path == "/api/config":
            new_cfg = dict(self.app.cfg)
            new_cfg.update(body.get("config") or {})
            self.app.cfg = new_cfg
            save_config(new_cfg)
            self.app.logs.write("[配置] 已保存")
            self._json({"ok": True, "config": new_cfg})

        elif path == "/api/scan":
            ok, msg = self.app.start_scan()
            self._json({"ok": ok, "msg": msg})

        elif path == "/api/monitor":
            action = (body.get("action") or "").lower()
            if action == "start":
                ok, msg = self.app.start_monitor()
            elif action == "stop":
                ok, msg = self.app.stop_monitor()
            else:
                ok, msg = False, "未知 action"
            self._json({"ok": ok, "msg": msg, "state": self.app.state()})

        elif path == "/api/test-ai":
            ok, msg = self.app.test_ai()
            self._json({"ok": ok, "msg": msg})

        elif path == "/api/pick-folder":
            initial = body.get("initial") or ""
            p = self.app.pick_folder(initial)
            self._json({"ok": bool(p), "path": p})

        elif path == "/api/open-folder":
            target = (body.get("path") or "").strip()
            if target and Path(target).is_dir():
                try:
                    if sys.platform.startswith("win"):
                        os.startfile(target)  # type: ignore[attr-defined]
                    elif sys.platform == "darwin":
                        import subprocess
                        subprocess.Popen(["open", target])
                    else:
                        import subprocess
                        subprocess.Popen(["xdg-open", target])
                    self._json({"ok": True})
                except Exception as e:
                    self._json({"ok": False, "msg": str(e)})
            else:
                self._json({"ok": False, "msg": "目录不存在"})

        else:
            self._json({"ok": False, "msg": "Not Found"}, 404)


# ======================================================================
#  单页前端
# ======================================================================

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VideoSorter · 视频自动分类管理</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎬</text></svg>">
<style>
  :root{
    --bg:#0f172a; --panel:#1e293b; --panel2:#273449; --line:#334155;
    --fg:#e2e8f0; --muted:#94a3b8; --accent:#38bdf8; --accent2:#0ea5e9;
    --ok:#34d399; --warn:#fbbf24; --err:#f87171;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;font-size:14px}
  header{padding:14px 22px;border-bottom:1px solid var(--line);
         display:flex;align-items:center;gap:14px;background:#0b1220}
  header h1{margin:0;font-size:17px;font-weight:600;letter-spacing:.5px}
  header .badge{padding:3px 10px;border-radius:99px;font-size:12px;
                background:var(--panel2);color:var(--muted);border:1px solid var(--line)}
  header .badge.on{background:#064e3b;color:var(--ok);border-color:#065f46}
  header .badge.busy{background:#78350f;color:var(--warn);border-color:#92400e}
  header .spacer{flex:1}

  main{display:grid;grid-template-columns:420px 1fr;gap:14px;padding:14px;height:calc(100vh - 58px)}
  @media (max-width:1000px){ main{grid-template-columns:1fr;height:auto} }

  .panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;
         padding:14px;overflow:auto}
  .panel h2{margin:0 0 10px;font-size:13px;text-transform:uppercase;
            letter-spacing:1px;color:var(--muted);font-weight:600}

  .row{display:flex;gap:6px;align-items:center;margin-bottom:8px}
  .row label{width:92px;color:var(--muted);font-size:13px;flex:none}
  input[type=text],input[type=password],select{
    flex:1;min-width:0;background:#0b1220;color:var(--fg);
    border:1px solid var(--line);border-radius:6px;padding:7px 9px;font-size:13px;outline:none}
  input:focus,select:focus{border-color:var(--accent)}
  input:disabled,select:disabled,textarea:disabled{opacity:.45;cursor:not-allowed}
  button{background:var(--panel2);color:var(--fg);border:1px solid var(--line);
         border-radius:6px;padding:7px 13px;cursor:pointer;font-size:13px;white-space:nowrap}
  button:hover{background:#334155}
  button:disabled{opacity:.4;cursor:not-allowed}
  button.primary{background:var(--accent2);border-color:var(--accent);color:#001824;font-weight:600}
  button.primary:hover{background:var(--accent)}
  button.danger{background:#7f1d1d;border-color:#991b1b}
  .btn-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}

  .checkline{display:flex;align-items:center;gap:7px;margin:6px 0;color:var(--muted);font-size:13px}
  .checkline input{accent-color:var(--accent)}

  .hint{font-size:12px;color:var(--muted);margin:4px 0 8px;line-height:1.5}

  /* 👇 留言板 textarea 样式 */
  textarea{
    width:100%;background:#0b1220;color:var(--fg);border:1px solid var(--line);
    border-radius:6px;padding:8px 10px;font-size:13px;outline:none;resize:vertical;
    font-family:Consolas,"Microsoft YaHei",monospace;line-height:1.6;min-height:110px
  }
  textarea:focus{border-color:var(--accent)}
  .notes-status{font-size:12px;color:var(--muted);align-self:center}

  #log{background:#050a14;border:1px solid var(--line);border-radius:8px;
       padding:10px;height:calc(100vh - 190px);overflow:auto;
       font-family:Consolas,"Courier New",monospace;font-size:12.5px;line-height:1.55;
       white-space:pre-wrap;word-break:break-all}
  @media (max-width:1000px){ #log{height:52vh} }
  #log .ok{color:var(--ok)} #log .err{color:var(--err)} #log .warn{color:var(--warn)}
  #log .new{color:var(--accent)} #log .muted{color:var(--muted)}

  .toast{position:fixed;right:20px;bottom:20px;background:var(--panel2);
         border:1px solid var(--line);border-left:3px solid var(--accent);
         border-radius:8px;padding:11px 16px;max-width:420px;font-size:13px;
         box-shadow:0 8px 24px rgba(0,0,0,.45);display:none;z-index:99}
  .toast.show{display:block;animation:fadeIn .18s}
  @keyframes fadeIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
</style>
</head>
<body>

<header>
  <h1>🎬 VideoSorter</h1>
  <span id="badge-state" class="badge">就绪</span>
  <span id="badge-ai" class="badge">AI 关闭</span>
  <span id="badge-monitor" class="badge">监控停止</span>
  <span class="spacer"></span>
  <span id="badge-stats" class="badge">成功 0 / 失败 0 / 跳过 0</span>
</header>

<main>
  <!-- ============ 左侧配置 ============ -->
  <section class="panel">
    <h2>目录</h2>
    <div class="row">
      <label>源文件夹</label>
      <input id="src" type="text" placeholder="D:\下载">
      <button onclick="pick('source')">浏览</button>
    </div>
    <div class="row">
      <label>目标库</label>
      <input id="tgt" type="text" placeholder="D:\视频库">
      <button onclick="pick('target')">浏览</button>
    </div>
    <div class="hint">提示：浏览器无法直选服务器路径，这里会弹出服务器端文件选择框（本地运行时可用）；也可直接粘贴路径。</div>

    <h2 style="margin-top:18px">AI 判定（可选）</h2>
    <div class="checkline">
      <input id="ai_enabled" type="checkbox">
      <span>启用 AI 判断分类（关闭时使用本地关键字匹配）</span>
    </div>

    <div class="row">
      <label>服务商</label>
      <select id="ai_provider"></select>
    </div>
    <div class="row">
      <label>接口地址</label>
      <input id="ai_base_url" type="text">
    </div>
    <div class="row">
      <label>API Key</label>
      <input id="ai_api_key" type="password" placeholder="sk-...">
    </div>
    <div class="row">
      <label>模型</label>
      <select id="ai_model_sel"></select>
      <input id="ai_model_custom" type="text" placeholder="自定义模型" style="display:none">
    </div>
    <div class="row">
      <label>超时(秒)</label>
      <input id="ai_timeout" type="text" value="30" style="max-width:90px">
      <button onclick="testAI()">测试连接</button>
    </div>
    <div id="provider-note" class="hint"></div>

    <!-- ==================== 👇 分类需求留言板 ==================== -->
    <h2 style="margin-top:18px">📝 分类需求留言板</h2>
    <div class="hint">
      在这里写下你对分类的额外要求（一行一条、随便写都行）。
      每次 AI 分类时会以「优先级高于默认规则」的方式一起发给 AI。<br>
      · 示例：<code>游戏视频一律按游戏英文名建文件夹</code>；
      · 示例：<code>标题含「预告」「PV」的短视频归入「预告片」，不要跟正片混</code>。
    </div>
    <textarea id="ai_notes"
      placeholder="示例：&#10;1. 游戏视频按游戏英文名建文件夹，不带版本号&#10;2. 标题含「教程 / 教学 / Tutorial」的归入「教程」分类&#10;3. 纪录片不要按年份分，统一进「纪录片」&#10;4. 带「预告」「PV」的短视频进「预告片」，不要跟正片混&#10;5. 无法判断的文件不要动，保持原样"></textarea>
    <div class="btn-row" style="margin-top:6px">
      <button onclick="saveNotes()">💾 保存留言板</button>
      <span id="notes-status" class="notes-status"></span>
    </div>
    <!-- ==================== 👆 留言板结束 ==================== -->

    <h2 style="margin-top:18px">运行选项</h2>
    <div class="checkline"><input id="recursive" type="checkbox"><span>递归扫描子目录</span></div>
    <div class="checkline"><input id="dry_run" type="checkbox"><span>演练模式（只打印不移动）</span></div>
    <div class="checkline"><input id="copy_mode" type="checkbox"><span>复制而非移动</span></div>
    <div class="checkline"><input id="create_new_folder" type="checkbox"><span>未命中时自动新建文件夹</span></div>
    <div class="row" style="margin-top:10px">
      <label>监控间隔(秒)</label>
      <input id="monitor_interval" type="text" value="3" style="max-width:90px">
    </div>

    <div class="btn-row">
      <button class="primary" id="btn-scan" onclick="doScan()">▶ 立即扫描分类</button>
      <button id="btn-monitor" onclick="toggleMonitor()">👁 开始监控</button>
    </div>
    <div class="btn-row">
      <button onclick="saveConfig()">💾 保存配置</button>
      <button onclick="openFolder('source')">📂 打开源目录</button>
      <button onclick="openFolder('target')">📂 打开目标库</button>
    </div>
  </section>

  <!-- ============ 右侧日志 ============ -->
  <section class="panel" style="display:flex;flex-direction:column">
    <h2 style="display:flex;align-items:center;gap:10px">
      运行日志 <span class="spacer" style="flex:1"></span>
      <button style="padding:4px 10px;font-size:12px" onclick="clearLog()">清空</button>
    </h2>
    <div id="log"></div>
  </section>
</main>

<div id="toast" class="toast"></div>

<script>
// ==================== 全局状态 ====================
let S = { config:{}, presets:{}, state:{}, logSeq:0 };

// ==================== 基础请求 ====================
async function api(path, body, method){
  const m = method || (body ? 'POST' : 'GET');
  const opt = body ? {
    method: m,
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)
  } : { method: m };
  const r = await fetch(path, opt);
  if(!r.ok) throw new Error('HTTP ' + r.status);
  return await r.json();
}

function toast(msg, ms=3200){
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(()=>el.classList.remove('show'), ms);
}

// ==================== 初始化 ====================
async function bootstrap(){
  const d = await api('/api/bootstrap');
  S.config  = d.config;
  S.presets = d.presets;
  S.state   = d.state;

  buildProviderSelect();
  applyConfigToUI();
  bindEvents();
  refreshState();
  pollLogs();

  addLog('[系统] 配置文件：' + d.config_file, 'muted');
  addLog('[系统] 界面已就绪，请设置源/目标目录后点击「立即扫描分类」', 'muted');
}

function buildProviderSelect(){
  const sel = document.getElementById('ai_provider');
  sel.innerHTML = '';
  for(const [key, p] of Object.entries(S.presets)){
    const o = document.createElement('option');
    o.value = key;
    o.textContent = p.label;
    sel.appendChild(o);
  }
}

function fillModels(providerKey, currentModel){
  const p = S.presets[providerKey] || {models:[]};
  const sel  = document.getElementById('ai_model_sel');
  const cust = document.getElementById('ai_model_custom');
  sel.innerHTML = '';
  const models = p.models || [];
  for(const m of models){
    const o = document.createElement('option');
    o.value = m; o.textContent = m;
    sel.appendChild(o);
  }
  if(!models.length || (currentModel && !models.includes(currentModel))){
    const o = document.createElement('option');
    o.value = '__custom__'; o.textContent = '（自定义…）';
    sel.appendChild(o);
  }
  if(currentModel && models.includes(currentModel)){
    sel.value = currentModel;
    cust.style.display = 'none';
  } else if(currentModel){
    sel.value = '__custom__';
    cust.style.display = '';
    cust.value = currentModel;
  } else if(models.length){
    sel.value = models[0];
    cust.style.display = 'none';
  }
  document.getElementById('provider-note').textContent = p.note || '';
}

// ==================== 配置 → UI ====================
function applyConfigToUI(){
  const c = S.config;
  document.getElementById('src').value = c.source_dir || '';
  document.getElementById('tgt').value = c.target_dir || '';
  document.getElementById('recursive').checked = !!c.recursive;
  document.getElementById('dry_run').checked = !!c.dry_run;
  document.getElementById('copy_mode').checked = c.move_mode === 'copy';
  document.getElementById('create_new_folder').checked = c.create_new_folder !== false;
  document.getElementById('monitor_interval').value = c.monitor_interval || 3;

  document.getElementById('ai_enabled').checked = !!c.ai_enabled;
  document.getElementById('ai_provider').value = c.ai_provider || 'deepseek';
  document.getElementById('ai_base_url').value = c.ai_base_url || '';
  document.getElementById('ai_api_key').value = c.ai_api_key || '';
  document.getElementById('ai_timeout').value = c.ai_timeout || 30;
  fillModels(c.ai_provider || 'deepseek', c.ai_model || '');

  // 👇 留言板内容回填
  document.getElementById('ai_notes').value = c.ai_notes || '';

  updateAIDisabled();
}

// ==================== UI → 配置 ====================
function collectConfig(){
  const c = Object.assign({}, S.config);
  c.source_dir = document.getElementById('src').value.trim();
  c.target_dir = document.getElementById('tgt').value.trim();
  c.recursive  = document.getElementById('recursive').checked;
  c.dry_run    = document.getElementById('dry_run').checked;
  c.move_mode  = document.getElementById('copy_mode').checked ? 'copy' : 'move';
  c.create_new_folder = document.getElementById('create_new_folder').checked;
  try{ c.monitor_interval = Math.max(1, parseInt(document.getElementById('monitor_interval').value) || 3); }catch(e){ c.monitor_interval = 3; }

  c.ai_enabled  = document.getElementById('ai_enabled').checked;
  c.ai_provider = document.getElementById('ai_provider').value;
  c.ai_base_url = document.getElementById('ai_base_url').value.trim();
  c.ai_api_key  = document.getElementById('ai_api_key').value.trim();
  const sel  = document.getElementById('ai_model_sel');
  const cust = document.getElementById('ai_model_custom');
  c.ai_model = (sel.value === '__custom__') ? cust.value.trim() : sel.value;
  try{ c.ai_timeout = Math.max(1, parseInt(document.getElementById('ai_timeout').value) || 30); }catch(e){ c.ai_timeout = 30; }

  // 👇 读取留言板内容
  c.ai_notes = document.getElementById('ai_notes').value;

  return c;
}

// ==================== 事件绑定 ====================
function bindEvents(){
  const provider = document.getElementById('ai_provider');
  provider.addEventListener('change', ()=>{
    const key = provider.value;
    const p = S.presets[key] || {};
    if(key !== 'custom'){
      document.getElementById('ai_base_url').value = p.base_url || '';
      fillModels(key, p.model || '');
    } else {
      fillModels(key, '');
    }
    document.getElementById('provider-note').textContent = p.note || '';
  });

  const sel = document.getElementById('ai_model_sel');
  sel.addEventListener('change', ()=>{
    const cust = document.getElementById('ai_model_custom');
    cust.style.display = (sel.value === '__custom__') ? '' : 'none';
    if(sel.value === '__custom__') cust.focus();
  });

  document.getElementById('ai_enabled').addEventListener('change', updateAIDisabled);

  // 👇 Ctrl+S 保存整份配置
  document.addEventListener('keydown', (e)=>{
    if((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's'){
      e.preventDefault();
      saveConfig();
    }
  });
}

function updateAIDisabled(){
  const on = document.getElementById('ai_enabled').checked;
  const ids = ['ai_provider','ai_base_url','ai_api_key','ai_model_sel','ai_model_custom','ai_timeout'];
  for(const id of ids){
    const el = document.getElementById(id);
    if(el) el.disabled = !on;
  }
  // 留言板独立于 AI 开关，始终可编辑
  document.getElementById('badge-ai').textContent = on ? 'AI 已开启' : 'AI 关闭';
  document.getElementById('badge-ai').className = 'badge' + (on ? ' on' : '');
}

// ==================== 操作 ====================
async function saveConfig(silent){
  try{
    const c = collectConfig();
    const r = await api('/api/config', {config:c});
    S.config = r.config;
    if(!silent) toast('配置已保存');
    return true;
  }catch(e){
    if(!silent) toast('保存失败：' + e.message);
    return false;
  }
}

// 👇 保存留言板（带状态反馈）
async function saveNotes(){
  const st = document.getElementById('notes-status');
  st.textContent = '保存中…';
  const ok = await saveConfig(true);
  if(ok){
    st.textContent = '✅ 已保存 ' + new Date().toLocaleTimeString();
    toast('留言板已保存');
  }else{
    st.textContent = '❌ 保存失败';
    toast('留言板保存失败');
  }
}

async function doScan(){
  if(!await saveConfig(true)) return;
  const btn = document.getElementById('btn-scan');
  btn.disabled = true;
  try{
    const r = await api('/api/scan', {}, 'POST');
    toast(r.ok ? r.msg : ('启动失败：' + r.msg));
    if(!r.ok) btn.disabled = false;
  }catch(e){
    toast('请求失败：' + e.message);
    btn.disabled = false;
  }
  refreshState();
}

async function toggleMonitor(){
  await saveConfig(true);
  const running = S.state.monitor_running;
  try{
    const r = await api('/api/monitor', {action: running ? 'stop' : 'start'});
    toast(r.msg);
    updateMonitorBtn(r.state && r.state.monitor_running);
  }catch(e){
    toast('请求失败：' + e.message);
  }
  refreshState();
}

async function testAI(){
  if(!await saveConfig(true)) return;
  toast('正在测试 AI 连接…', 8000);
  try{
    const r = await api('/api/test-ai', {}, 'POST');
    toast(r.msg, 6000);
    addLog('[AI] ' + r.msg, r.ok ? 'ok' : 'err');
  }catch(e){
    toast('请求失败：' + e.message);
  }
}

async function pick(kind){
  const init = kind === 'source'
      ? document.getElementById('src').value
      : document.getElementById('tgt').value;
  toast('请在弹出窗口中选择文件夹…', 2500);
  try{
    const r = await api('/api/pick-folder', {initial: init});
    if(r.ok && r.path){
      document.getElementById(kind === 'source' ? 'src' : 'tgt').value = r.path;
      toast('已选择：' + r.path);
    } else {
      toast('未选择路径，请手动输入');
    }
  }catch(e){
    toast('请求失败：' + e.message);
  }
}

async function openFolder(kind){
  const p = document.getElementById(kind === 'source' ? 'src' : 'tgt').value.trim();
  if(!p){ toast('路径为空'); return; }
  try{
    const r = await api('/api/open-folder', {path:p});
    if(!r.ok) toast('打开失败：' + (r.msg || ''));
  }catch(e){ toast('请求失败：' + e.message); }
}

function clearLog(){
  document.getElementById('log').innerHTML = '';
}

// ==================== 状态轮询 ====================
async function refreshState(){
  try{
    const st = await api('/api/state');
    S.state = st;

    const bState = document.getElementById('badge-state');
    if(st.busy){
      bState.textContent = '扫描中…';
      bState.className = 'badge busy';
      document.getElementById('btn-scan').disabled = true;
    } else {
      bState.textContent = '就绪';
      bState.className = 'badge on';
      document.getElementById('btn-scan').disabled = false;
    }

    updateMonitorBtn(st.monitor_running);

    const s = st.stats || {};
    document.getElementById('badge-stats').textContent =
      `成功 ${s.ok||0} / 失败 ${s.fail||0} / 跳过 ${s.skip||0}`;

  }catch(e){ /* 忽略 */ }
}

function updateMonitorBtn(running){
  const btn = document.getElementById('btn-monitor');
  const bm  = document.getElementById('badge-monitor');
  if(running){
    btn.textContent = '⏹ 停止监控';
    btn.classList.add('danger');
    bm.textContent = '监控中';
    bm.className = 'badge on';
  } else {
    btn.textContent = '👁 开始监控';
    btn.classList.remove('danger');
    bm.textContent = '监控停止';
    bm.className = 'badge';
  }
}

setInterval(refreshState, 1500);

// ==================== 日志轮询 ====================
async function pollLogs(){
  try{
    const d = await api('/api/logs?since=' + S.logSeq);
    if(d.lines && d.lines.length){
      for(const item of d.lines) addLog(item.t);
      S.logSeq = d.seq;
    } else if(typeof d.seq === 'number'){
      S.logSeq = d.seq;
    }
  }catch(e){ /* 忽略 */ }
  setTimeout(pollLogs, 800);
}

function addLog(text, cls){
  const box = document.getElementById('log');
  const div = document.createElement('div');
  if(cls) div.className = cls;
  else if(text.includes('[错误]') || text.includes('失败')) div.className = 'err';
  else if(text.includes('[完成]')) div.className = 'ok';
  else if(text.includes('[演练]') || text.includes('[监控]')) div.className = 'warn';
  else if(text.includes('＋')) div.className = 'new';
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
  while(box.childElementCount > 1500) box.removeChild(box.firstChild);
}

// ==================== 启动 ====================
bootstrap().catch(e => toast('初始化失败：' + e.message));
</script>
</body>
</html>
"""


# ======================================================================
#  CLI 模式
# ======================================================================

def run_cli(cfg: dict, watch: bool = False):
    def log(msg):
        print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)

    err = validate_config(cfg)
    if err:
        print(f"配置错误：{err}", file=sys.stderr)
        sys.exit(1)

    sorter = Sorter(cfg, log)
    if not watch:
        sorter.scan_once()
        return

    stop_event = threading.Event()
    worker = MonitorWorker(sorter, cfg.get("monitor_interval", 3), stop_event, log)
    worker.start()
    log("按 Ctrl+C 退出监控")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        log("收到中断信号，正在退出…")
        stop_event.set()
        worker.join(timeout=5)


# ======================================================================
#  入口
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description="VideoSorter Web —— 视频自动分类管理工具（Web 版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  python video_sorter_web.py                              打开 Web 界面
  python video_sorter_web.py --port 9000                  指定端口
  python video_sorter_web.py --no-browser                 不自动打开浏览器
  python video_sorter_web.py --cli -s D:\\下载 -t D:\\视频库  命令行扫描一次
  python video_sorter_web.py --cli -s D:\\下载 -t D:\\视频库 --watch -i 5
  python video_sorter_web.py --cli -s D:\\下载 -t D:\\视频库 --ai --provider zhipu --api-key xxx
""")
    parser.add_argument("--cli", action="store_true", help="命令行模式")
    parser.add_argument("-s", "--source", help="源文件夹")
    parser.add_argument("-t", "--target", help="目标库")
    parser.add_argument("--ai", action="store_true", help="启用 AI 判断")
    parser.add_argument("--provider", help="AI 服务商（deepseek/qwen/zhipu/siliconflow/moonshot/openai/ollama/custom）")
    parser.add_argument("--api-base", help="AI 接口地址")
    parser.add_argument("--api-key", help="AI API Key")
    parser.add_argument("--model", help="AI 模型名")
    parser.add_argument("--watch", action="store_true", help="持续监控源目录")
    parser.add_argument("-i", "--interval", type=int, help="监控扫描间隔（秒）")
    parser.add_argument("--dry-run", action="store_true", help="演练模式")
    parser.add_argument("--recursive", action="store_true", help="递归扫描子目录")

    parser.add_argument("--port", type=int, help="Web 端口（默认 8765）")
    parser.add_argument("--host", help="Web 监听地址（默认 127.0.0.1）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    cfg = load_config()
    if args.source:      cfg["source_dir"] = args.source
    if args.target:      cfg["target_dir"] = args.target
    if args.dry_run:     cfg["dry_run"] = True
    if args.recursive:   cfg["recursive"] = True
    if args.interval:    cfg["monitor_interval"] = args.interval
    if args.api_base:    cfg["ai_base_url"] = args.api_base
    if args.api_key:     cfg["ai_api_key"] = args.api_key
    if args.model:       cfg["ai_model"] = args.model

    if args.provider and args.provider in AI_PRESETS:
        preset = AI_PRESETS[args.provider]
        cfg["ai_provider"] = args.provider
        if not args.api_base and preset["base_url"]:
            cfg["ai_base_url"] = preset["base_url"]
        if not args.model and preset["model"]:
            cfg["ai_model"] = preset["model"]

    if args.ai or args.provider:
        cfg["ai_enabled"] = True

    # ---------------- CLI ----------------
    if args.cli or args.watch:
        run_cli(cfg, watch=args.watch)
        return

    # ---------------- Web ----------------
    if args.port:
        cfg["web_port"] = args.port
    if args.host:
        cfg["web_host"] = args.host
    save_config(cfg)

    app = WebApp(cfg)
    host = cfg.get("web_host", "127.0.0.1")
    port = int(cfg.get("web_port", 8765))

    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        print(f"[错误] 无法监听 {host}:{port} —— {e}", file=sys.stderr)
        print("提示：换一个端口试试，例如 --port 8899", file=sys.stderr)
        sys.exit(1)

    httpd.app = app  # type: ignore[attr-defined]

    url = f"http://{host}:{port}/"
    print("=" * 62)
    print(f"  {APP_NAME} 已启动")
    print(f"  访问地址：{url}")
    print(f"  配置文件：{CONFIG_FILE}")
    print(f"  日志文件：{LOG_FILE}")
    print("  按 Ctrl+C 退出")
    print("=" * 62)

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭…")
        if app.stop_event is not None:
            app.stop_event.set()
        httpd.shutdown()


if __name__ == "__main__":
    main()
