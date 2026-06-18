"""
PDF Splitter Tool v3.2 - AI智能PDF拆分工具
===========================================
新增AI特性：
  1. 可配置的大模型API接入（支持OpenAI/DeepSeek/硅基流动等兼容接口）
  2. 自动识别扉页并提取标题
  3. 按扉页自动拆分PDF
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import json
import threading
import re
import base64


# ==================== 配置管理 ====================

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "api_base_url": "https://api.openai.com/v1",
    "api_key": "",
    "model": "gpt-4o-mini",
    "max_tokens": 4096,
    "temperature": 0.1
}


def load_config():
    """加载配置文件"""
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for k in DEFAULT_CONFIG:
                cfg.setdefault(k, DEFAULT_CONFIG[k])
            return cfg
        except Exception:
            return dict(DEFAULT_CONFIG)
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    """保存配置文件"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        messagebox.showerror("保存配置失败", str(e))
        return False


# ==================== LLM API 调用 ====================

def call_llm_api(config, system_prompt, user_content):
    """
    调用 OpenAI 兼容的大模型 API。
    user_content 可以是：
      - str: 纯文本模式
      - list: 多模态模式 [{"type":"text","text":"..."}, {"type":"image_url","image_url":{"url":"data:..."}}]
    返回解析后的 JSON 对象，或抛出异常
    """
    import urllib.request
    import urllib.error

    url = config["api_base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['api_key']}"
    }
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ],
        "max_tokens": config.get("max_tokens", 4096),
        "temperature": config.get("temperature", 0.1)
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise Exception(f"API 请求失败 (HTTP {e.code}): {body}")
    except urllib.error.URLError as e:
        raise Exception(f"网络连接失败: {e.reason}")
    except json.JSONDecodeError as e:
        raise Exception(f"API 返回无法解析: {e}")

    # 提取回复文本
    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise Exception(f"API 返回格式异常: {json.dumps(result, ensure_ascii=False)[:300]}")

    # 尝试从回复中提取 JSON（可能被 markdown 包裹）
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
    if json_match:
        json_str = json_match.group(1)
    else:
        # 尝试直接解析整段文本
        json_str = content.strip()

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        # 尝试找到第一个 { 和最后一个 }
        start = json_str.find("{")
        end = json_str.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(json_str[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise Exception(f"无法解析 AI 返回结果:\n{content[:500]}")


def render_page_to_b64(page, dpi=100, quality=60):
    """将PDF页面渲染为base64编码的JPEG图片"""
    import fitz
    pix = page.get_pixmap(dpi=dpi)
    img_bytes = pix.tobytes("jpeg", quality)
    return base64.b64encode(img_bytes).decode("ascii")


def detect_scanned_pdf(doc, threshold=0.5):
    """检测PDF是否为扫描件（超过threshold比例的页面无文字）"""
    total = len(doc)
    empty = 0
    for i in range(total):
        text = doc[i].get_text().strip()
        if len(text) < 10:
            empty += 1
    return (empty / total) >= threshold if total > 0 else False


# ==================== 扉页分析 Prompt ====================

SYSTEM_PROMPT_FEIYE = """# 扉页识别专家

你是PDF文档结构分析专家。你的任务是**先通读整份PDF的内容，全面理解文档结构，再判断哪些页面是扉页**。

## 工作流程

### 第一步：通读全文
仔细阅读下面提供的PDF每页完整文本内容，建立对整份文档的宏观理解：
- 文档的主题是什么（项目名称、报告标题等）？
- 文档包含几个章节/部分？
- 每页大致讲什么内容？

### 第二步：识别扉页
根据以下特征，在整体理解的基础上判断每一页是否为扉页：

**核心特征（按重要程度排列）：**

1. **页面稀疏度** — 扉页文字极少，整页通常只有一行或寥寥几行字，大面积空白
2. **底图/背景一致性** — 多个扉页通常使用相同的底图背景、相同的排版风格
3. **内容对比法** — 扉页后的下一页通常是大段正文内容，形成"稀疏→密集"的明显对比
4. **文字特征** — 标题常含章节标识（第X章、第X节、Chapter X等）或项目名称
5. **节奏规律** — 扉页有规律地间隔出现，通常是每章开头的标志

**识别技巧：**
- 封面页（PDF第1页）通常是扉页
- 文字量突然从极少跳到大量的转折点，往前翻一页往往是扉页
- 如果多页的文字结构看起来相似（都是简短标题），且被正文页隔开，这些就是扉页
- 有图片但文字极少的页面，很可能是带底图的扉页
- **宁可漏判也不要误判** — 拿不准的页面不要标记为扉页

## 输出要求
请输出严格的 JSON 格式（不要用 markdown 代码块包裹），格式如下：
{
  "title_pages": [
    {"page_number": 1, "title": "封面"},
    {"page_number": 5, "title": "第一章 绪论"},
    {"page_number": 12, "title": "第二章 文献综述"}
  ]
}

- page_number 是 PDF 中的页码（从1开始）
- title 是该扉页上的主要标题名称（取最核心的几个字即可）
- 如果没有任何扉页，返回 {"title_pages": []}"""

SYSTEM_PROMPT_EXTRACT_TITLE = """# 标题提取专家

用户已手动指定了PDF中的扉页页码。你的任务是**提取这些指定页面上的标题名称**。

## 要求
- 仔细阅读每个指定页面的文本内容
- 提取该页面上**最主要、最显眼的标题文字**作为名称
- 标题通常是一行独立的文字，可能是章节名、项目名、活动名等
- 去掉多余的修饰词和编号前缀

## 输出要求
请输出严格的 JSON 格式（不要用 markdown 代码块包裹），格式如下：
{
  "title_pages": [
    {"page_number": 1, "title": "封面"},
    {"page_number": 15, "title": "第一章 绪论"},
    {"page_number": 30, "title": "第二章 文献综述"}
  ]
}

- page_number 必须与用户指定的页码一一对应
- title 从该页文本中提取"""

SYSTEM_PROMPT_FEIYE_VISION = """# 扉页识别专家（图片识别模式）

你是PDF文档结构分析专家。我会给你PDF每页的截图，请**看图识别扉页**。

## 扉页特征
- 页面内容极其稀疏：大片空白/底图 + 居中一行标题文字
- 多个扉页通常有相同或相似的背景底图
- 与前后页形成"稀疏→密集"的明显对比
- 封面页（第1页）通常是扉页

## 输出要求
严格的 JSON（不要markdown包裹）：
{
  "title_pages": [
    {"page_number": 1, "title": "封面标题"},
    {"page_number": 5, "title": "第一章 XXX"}
  ]
}
没有扉页返回 {"title_pages": []}"""

SYSTEM_PROMPT_EXTRACT_VISION = """# 标题提取专家（图片识别模式）

用户已指定了扉页页码。请从每张页面截图中**提取标题文字**。

## 输出要求
严格的 JSON（不要markdown包裹）：
{
  "title_pages": [
    {"page_number": 1, "title": "封面标题"},
    {"page_number": 15, "title": "第一章 XXX"}
  ]
}
page_number 与用户指定页码一一对应，title 是从该页截图中读取的主要标题文字"""


# ==================== 主应用 ====================

class PDFSplitterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF 拆分工具 v3.2")
        self.root.geometry("960x700")
        self.root.minsize(860, 580)
        self.root.configure(bg="#F5F6F8")

        self.COLORS = {
            "bg": "#F5F6F8",
            "surface": "#FFFFFF",
            "primary": "#4A90D9",
            "primary_dark": "#3A7CC4",
            "primary_press": "#2E6AAE",
            "success": "#5CB85C",
            "danger": "#D9534F",
            "text": "#333333",
            "text_light": "#999999",
            "border": "#C8C8C8",
            "border_active": "#4A90D9",
            "header_bg": "#3B7DD8",
        }

        self.pdf_path = tk.StringVar()
        self.total_pages = tk.StringVar(value="-")
        self.split_count = tk.IntVar(value=2)
        self.split_entries = []
        self.config = load_config()
        self._page_texts_cache = []
        self.manual_pages_var = tk.StringVar()

        self.build_ui()

    def _make_btn(self, parent, text, command, width=None, color="default",
                  font_size=9, padx=16, pady=5):
        """统一制作按钮，带边框 + 悬停变色 + 按下凹陷特效"""
        if color == "primary":
            bg = self.COLORS["primary"]
            fg = "white"
            hover_bg = self.COLORS["primary_dark"]
            press_bg = self.COLORS["primary_press"]
            border_color = self.COLORS["primary_dark"]
        elif color == "danger":
            bg = self.COLORS["danger"]
            fg = "white"
            hover_bg = "#C9302C"
            press_bg = "#B52B27"
            border_color = "#C9302C"
        else:
            bg = self.COLORS["surface"]
            fg = self.COLORS["text"]
            hover_bg = "#EAF0F9"
            press_bg = "#D6E2F2"
            border_color = self.COLORS["border"]

        kw = dict(
            text=text, bg=bg, fg=fg, bd=1, relief="solid",
            font=("Microsoft YaHei UI", font_size), cursor="hand2",
            padx=padx, pady=pady,
            activebackground=press_bg, activeforeground=fg,
            highlightthickness=0,
        )
        if width:
            kw["width"] = width
        btn = tk.Button(parent, command=command, **kw)

        # 设置边框色（tkinter 用 highlightbackground 做静态边框色）
        try:
            btn.tk.call(btn._w, "configure", "-highlightbackground", border_color)
            btn.tk.call(btn._w, "configure", "-highlightcolor", border_color)
        except Exception:
            pass

        # ---- 悬停 & 按压特效 ----
        def _on_enter(e, b=btn, hbg=hover_bg, bc=border_color):
            b.configure(bg=hbg)
            try:
                b.tk.call(b._w, "configure", "-highlightbackground",
                          self.COLORS["border_active"])
            except Exception:
                pass

        def _on_leave(e, b=btn, obg=bg, bc=border_color):
            b.configure(bg=obg, relief="solid")
            try:
                b.tk.call(b._w, "configure", "-highlightbackground", bc)
            except Exception:
                pass

        def _on_press(e, b=btn, pbg=press_bg):
            b.configure(bg=pbg, relief="sunken")

        def _on_release(e, b=btn, obg=bg, bc=border_color):
            b.configure(bg=obg, relief="solid")

        btn.bind("<Enter>", _on_enter)
        btn.bind("<Leave>", _on_leave)
        btn.bind("<ButtonPress-1>", _on_press)
        btn.bind("<ButtonRelease-1>", _on_release)

        return btn

    # ==================== UI 构建 ====================

    def build_ui(self):
        # ===== 底部栏（最先 pack，保证不会被挤下去）=====
        bottom = tk.Frame(self.root, bg=self.COLORS["surface"], height=50,
                          highlightthickness=1, highlightbackground=self.COLORS["border"])
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)

        self.progress_bar = ttk.Progressbar(bottom, mode="determinate", length=150,
                                             style="TProgressbar")
        self.progress_bar.pack(side="left", padx=(14, 8))

        self.status_label = tk.Label(bottom, text="就绪",
                                     bg=self.COLORS["surface"],
                                     fg=self.COLORS["text_light"],
                                     font=("Microsoft YaHei UI", 9))
        self.status_label.pack(side="left")

        self._make_btn(bottom, "清空", self.clear_all, color="default").pack(side="right", padx=(0, 14))
        self.split_btn = self._make_btn(bottom, "开始拆分", self.start_split,
                                         color="primary")
        self.split_btn.pack(side="right", padx=6)

        # ===== 顶栏 =====
        header = tk.Frame(self.root, bg=self.COLORS["header_bg"], height=40)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(header, text="  PDF 拆分工具 v3.2",
                 fg="white", bg=self.COLORS["header_bg"],
                 font=("Microsoft YaHei UI", 11, "bold")).pack(side="left", pady=6)

        tk.Label(header, text="·  制作者：oyn",
                 fg="#B0D4FF", bg=self.COLORS["header_bg"],
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=4, pady=6)

        self.api_status_label = tk.Label(
            header, text=self._api_status_text(),
            fg="#B0D4FF", bg=self.COLORS["header_bg"],
            font=("Microsoft YaHei UI", 8)
        )
        self.api_status_label.pack(side="right", padx=(0, 6))

        self._make_btn(header, "API 设置", self.open_settings).pack(side="right", padx=6)

        # ===== 内容区 =====
        content = tk.Frame(self.root, bg=self.COLORS["bg"])
        content.pack(fill="both", expand=True, padx=10, pady=(6, 6))

        # --- 选择 PDF ---
        card1 = tk.Frame(content, bg=self.COLORS["surface"], bd=0,
                          highlightthickness=1, highlightbackground=self.COLORS["border"])
        card1.pack(fill="x", pady=(0, 6))

        h1 = tk.Frame(card1, bg=self.COLORS["surface"], padx=14, pady=10)
        h1.pack(fill="x")
        tk.Label(h1, text="选择 PDF 文件", bg=self.COLORS["surface"],
                 font=("Microsoft YaHei UI", 10, "bold"),
                 fg=self.COLORS["text"]).pack(side="left")
        tk.Label(h1, textvariable=self.total_pages,
                 fg=self.COLORS["primary"], bg=self.COLORS["surface"],
                 font=("Microsoft YaHei UI", 16, "bold")).pack(side="right")
        tk.Label(h1, text="总页数  ", bg=self.COLORS["surface"],
                 font=("Microsoft YaHei UI", 9),
                 fg=self.COLORS["text_light"]).pack(side="right")

        row1 = tk.Frame(card1, bg=self.COLORS["surface"], padx=14, pady=6)
        row1.pack(fill="x", pady=(0, 12))
        tk.Entry(row1, textvariable=self.pdf_path, relief="solid", bd=1,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._make_btn(row1, "浏览", self.browse_pdf).pack(side="left", padx=2)
        self._make_btn(row1, "加载", self.load_pdf_info).pack(side="left", padx=2)

        # --- 拆分方式 ---
        card2 = tk.Frame(content, bg=self.COLORS["surface"], bd=0,
                          highlightthickness=1, highlightbackground=self.COLORS["border"])
        card2.pack(fill="x", pady=6)

        h2 = tk.Frame(card2, bg=self.COLORS["surface"], padx=14, pady=10)
        h2.pack(fill="x")
        tk.Label(h2, text="拆分方式", bg=self.COLORS["surface"],
                 font=("Microsoft YaHei UI", 10, "bold"),
                 fg=self.COLORS["text"]).pack(side="left")

        inner = tk.Frame(card2, bg=self.COLORS["surface"], padx=14)
        inner.pack(fill="x", pady=(0, 12))

        # Row 0: AI
        r0 = tk.Frame(inner, bg=self.COLORS["surface"])
        r0.pack(fill="x", pady=2)
        tk.Label(r0, text="AI 自动识别", bg=self.COLORS["surface"],
                 width=12, anchor="e", font=("Microsoft YaHei UI", 9),
                 fg=self.COLORS["text"]).pack(side="left", padx=(0, 8))
        self._make_btn(r0, "AI 自动识别扉页", self.auto_analyze).pack(side="left")
        tk.Label(r0, text="   大模型分析全文，自动找出扉页",
                 bg=self.COLORS["surface"], fg=self.COLORS["text_light"],
                 font=("Microsoft YaHei UI", 8)).pack(side="left")

        # Row 1: Manual
        r1 = tk.Frame(inner, bg=self.COLORS["surface"])
        r1.pack(fill="x", pady=2)
        tk.Label(r1, text="手动指定", bg=self.COLORS["surface"],
                 width=12, anchor="e", font=("Microsoft YaHei UI", 9),
                 fg=self.COLORS["text"]).pack(side="left", padx=(0, 8))
        tk.Entry(r1, textvariable=self.manual_pages_var, width=14,
                 relief="solid", bd=1, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(0, 6))
        self._make_btn(r1, "按页码切分", self.manual_title_pages).pack(side="left")
        tk.Label(r1, text="   逗号分隔，如 1,15,30,40,47",
                 bg=self.COLORS["surface"], fg=self.COLORS["text_light"],
                 font=("Microsoft YaHei UI", 8)).pack(side="left")

        # Row 2: Count
        r2 = tk.Frame(inner, bg=self.COLORS["surface"])
        r2.pack(fill="x", pady=2)
        tk.Label(r2, text="按数量拆分", bg=self.COLORS["surface"],
                 width=12, anchor="e", font=("Microsoft YaHei UI", 9),
                 fg=self.COLORS["text"]).pack(side="left", padx=(0, 8))
        spb = ttk.Spinbox(r2, from_=1, to=200, textvariable=self.split_count,
                          width=5, state="readonly")
        spb.pack(side="left")
        tk.Label(r2, text="  个  ", bg=self.COLORS["surface"],
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self._make_btn(r2, "生成选项", self.generate_entries).pack(side="left", padx=(6, 0))

        # --- 拆分详情 ---
        card3 = tk.Frame(content, bg=self.COLORS["surface"], bd=0,
                          highlightthickness=1, highlightbackground=self.COLORS["border"])
        card3.pack(fill="both", expand=True)

        h3 = tk.Frame(card3, bg=self.COLORS["surface"], padx=14, pady=8)
        h3.pack(fill="x", pady=(10, 6))
        tk.Label(h3, text="拆分详情", bg=self.COLORS["surface"],
                 font=("Microsoft YaHei UI", 10, "bold"),
                 fg=self.COLORS["text"]).pack(side="left")
        self._make_btn(h3, "设置统一保存路径", self.set_unified_path).pack(side="right")

        # 表头
        hdr = tk.Frame(card3, bg="#F9F9F9", height=26)
        hdr.pack(fill="x", padx=14)
        hdr.pack_propagate(False)
        for text, w in [("  文件名称", 26), ("起始页", 7), ("结束页", 7), ("保存路径", 52)]:
            tk.Label(hdr, text=text, bg="#F9F9F9", fg=self.COLORS["text"],
                     font=("Microsoft YaHei UI", 9, "bold"),
                     width=w, anchor="w").pack(side="left", padx=1)

        # 可滚动
        cw = tk.Frame(card3, bg=self.COLORS["surface"])
        cw.pack(fill="both", expand=True, padx=14, pady=(2, 8))

        self.canvas = tk.Canvas(cw, highlightthickness=0, bg=self.COLORS["surface"])
        self.scrollbar = ttk.Scrollbar(cw, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg=self.COLORS["surface"])

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas_window = self.canvas.create_window(
            (0, 0), window=self.scrollable_frame, anchor="nw", tags="inner"
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self._bind_mousewheel()

        self.placeholder = tk.Label(
            self.scrollable_frame,
            text="点击「AI 自动识别」或「手动指定页码」开始拆分",
            fg=self.COLORS["text_light"], bg=self.COLORS["surface"],
            font=("Microsoft YaHei UI", 10)
        )
        self.placeholder.pack(pady=36)

    def _api_status_text(self):
        key = self.config.get("api_key", "")
        model = self.config.get("model", "未设置")
        if key:
            masked = key[:6] + "..." if len(key) > 10 else key[:4] + "..."
            return f" 已配置  |  {model}  |  {masked}"
        else:
            return "  未配置 API Key"

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)

    def _bind_mousewheel(self):
        def _on_mousewheel(event):
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.canvas.bind_all("<MouseWheel>", _on_mousewheel)
        self.root.bind("<Destroy>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    # ==================== API 设置对话框 ====================

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("API 设置")
        win.geometry("520x320")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        frame = ttk.Frame(win, padding=20)
        frame.pack(fill="both", expand=True)

        # API Base URL
        ttk.Label(frame, text="API Base URL：").grid(row=0, column=0, sticky="e", pady=6)
        api_url_var = tk.StringVar(value=self.config.get("api_base_url", ""))
        ttk.Entry(frame, textvariable=api_url_var, width=50).grid(row=0, column=1, padx=(8, 0), pady=6)
        ttk.Label(frame, text="例如：https://api.openai.com/v1", foreground="#999",
                  font=("", 8)).grid(row=0, column=1, sticky="w", padx=(8, 0), pady=(26, 0))

        # API Key
        ttk.Label(frame, text="API Key：").grid(row=1, column=0, sticky="e", pady=6)
        api_key_var = tk.StringVar(value=self.config.get("api_key", ""))
        key_entry = ttk.Entry(frame, textvariable=api_key_var, width=50, show="*")
        key_entry.grid(row=1, column=1, padx=(8, 0), pady=6)

        def toggle_key_visibility():
            if key_entry.cget("show") == "*":
                key_entry.configure(show="")
                toggle_btn.configure(text="🙈 隐藏")
            else:
                key_entry.configure(show="*")
                toggle_btn.configure(text="👁️ 显示")

        toggle_btn = ttk.Button(frame, text="👁️ 显示", command=toggle_key_visibility, width=8)
        toggle_btn.grid(row=1, column=2, padx=(4, 0), pady=6)

        # Model（纯文本输入，不预设选项）
        ttk.Label(frame, text="模型名称：").grid(row=2, column=0, sticky="e", pady=6)
        model_var = tk.StringVar(value=self.config.get("model", ""))
        ttk.Entry(frame, textvariable=model_var, width=50).grid(row=2, column=1, padx=(8, 0), pady=6)
        ttk.Label(frame, text="请输入模型名称，如 gpt-4o、deepseek-chat、claude-sonnet-4-20250514 等",
                  foreground="#999", font=("", 8)).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(26, 0))

        # 分隔线和提示
        ttk.Separator(frame, orient="horizontal").grid(row=3, column=0, columnspan=3,
                                                        sticky="ew", pady=(12, 6))
        ttk.Label(frame, text="💡 提示：AI功能需要大模型API支持，请确保填入有效的API Key",
                  foreground="#0078D4", font=("", 8)).grid(row=4, column=0, columnspan=3, pady=4)

        # 按钮
        btn_row = ttk.Frame(frame)
        btn_row.grid(row=5, column=0, columnspan=3, pady=(12, 0))

        def do_save():
            self.config["api_base_url"] = api_url_var.get().strip()
            self.config["api_key"] = api_key_var.get().strip()
            self.config["model"] = model_var.get().strip()
            if save_config(self.config):
                self.api_status_label.configure(text=self._api_status_text())
                win.destroy()

        ttk.Button(btn_row, text="💾 保存", command=do_save, width=12).pack(side="left", padx=4)
        ttk.Button(btn_row, text="取消", command=win.destroy, width=8).pack(side="left", padx=4)

    # ==================== PDF 操作 ====================

    def browse_pdf(self):
        path = filedialog.askopenfilename(
            title="选择PDF文件",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")]
        )
        if path:
            self.pdf_path.set(path)
            # 选择文件后自动加载PDF信息
            self.load_pdf_info()

    def load_pdf_info(self):
        path = self.pdf_path.get()
        if not path:
            # 没有路径时自动弹出文件选择框
            path = filedialog.askopenfilename(
                title="选择PDF文件",
                filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")]
            )
            if path:
                self.pdf_path.set(path)
            else:
                return
        if not os.path.isfile(path):
            messagebox.showerror("错误", "文件不存在，请重新选择")
            return

        def _load():
            try:
                import fitz
                doc = fitz.open(path)
                pages = len(doc)
                self.root.after(0, lambda: self.total_pages.set(str(pages)))
                self.root.after(0, lambda: self.status_label.config(
                    text=f"已加载：{os.path.basename(path)}（共 {pages} 页）", foreground="#333"))
                doc.close()
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("加载失败", str(e)))

        self.status_label.config(text="正在加载PDF...", foreground="#0078D4")
        threading.Thread(target=_load, daemon=True).start()

    def generate_entries(self, title_data=None):
        """
        生成输入行。
        如果提供了 title_data（AI分析结果），则按扉页拆分并填入名称。
        """
        if hasattr(self, "placeholder") and self.placeholder.winfo_exists():
            self.placeholder.destroy()

        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.split_entries.clear()

        try:
            total_p = int(self.total_pages.get())
        except Exception:
            total_p = 0

        if title_data:
            # === AI模式：按扉页拆分 ===
            title_pages = title_data.get("title_pages", [])
            if not title_pages:
                messagebox.showinfo("提示", "AI 未识别到任何扉页，请手动设置。")
                self.split_count.set(1)
                title_data = None
                self.generate_entries()
                return

            # 排序
            title_pages.sort(key=lambda x: x.get("page_number", 1))

            n = len(title_pages)
            self.split_count.set(n)

            for i, tp in enumerate(title_pages):
                row_frame = tk.Frame(self.scrollable_frame, bg=self.COLORS["surface"])
                row_frame.pack(fill="x", pady=1, ipady=2)

                name = tp.get("title", f"扉页_{i+1}")
                sp = tp.get("page_number", 1)

                # 结束页：下一个扉页的上一页，或 PDF 最后一页
                if i + 1 < len(title_pages):
                    ep = title_pages[i + 1].get("page_number", total_p) - 1
                else:
                    ep = total_p

                # 如果结束页小于起始页，修正
                if ep < sp:
                    ep = sp

                idx_label = tk.Label(row_frame, text=f"#{i+1}", width=3, anchor="e",
                                      font=("Microsoft YaHei UI", 8, "bold"),
                                      fg=self.COLORS["primary"], bg=self.COLORS["surface"])
                idx_label.pack(side="left", padx=(0, 4))

                name_var = tk.StringVar(value=name)
                start_var = tk.StringVar(value=str(sp))
                end_var = tk.StringVar(value=str(ep))
                path_var = tk.StringVar()

                tk.Entry(row_frame, textvariable=name_var, width=24,
                         relief="solid", bd=1, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=2)
                tk.Entry(row_frame, textvariable=start_var, width=7,
                         relief="solid", bd=1, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=2)
                tk.Entry(row_frame, textvariable=end_var, width=7,
                         relief="solid", bd=1, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=2)

                tk.Entry(row_frame, textvariable=path_var, width=44,
                         relief="solid", bd=1, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=2)

                self._make_btn(row_frame, "选择",
                               command=lambda v=path_var, nv=name_var: self.browse_save(v, nv),
                               font_size=8, padx=8, pady=2).pack(side="left", padx=2)

                self.split_entries.append({
                    "name": name_var,
                    "start": start_var,
                    "end": end_var,
                    "path": path_var
                })

                # 名称变更时自动同步保存路径
                name_var.trace_add("write", lambda *a, nv=name_var, pv=path_var:
                                   self._sync_path_on_name_change(nv, pv))

            self.status_label.config(
                text=f"✅ AI自动识别完成！检测到 {n} 个扉页，已自动填入。请确认后点击「开始拆分」",
                foreground="#00A000")

        else:
            # === 手动模式：按数量平均拆分 ===
            try:
                n = self.split_count.get()
            except Exception:
                n = 2
            n = max(1, min(n, 200))

            if total_p > 0:
                base = total_p // n
                remainder = total_p % n
            else:
                base = 0
                remainder = 0

            for i in range(1, n + 1):
                row_frame = tk.Frame(self.scrollable_frame, bg=self.COLORS["surface"])
                row_frame.pack(fill="x", pady=1, ipady=2)

                idx_label = tk.Label(row_frame, text=f"#{i}", width=3, anchor="e",
                                      font=("Microsoft YaHei UI", 8, "bold"),
                                      fg=self.COLORS["primary"], bg=self.COLORS["surface"])
                idx_label.pack(side="left", padx=(0, 4))

                name_var = tk.StringVar(value=f"拆分文件_{i}")
                start_var = tk.StringVar()
                end_var = tk.StringVar()
                path_var = tk.StringVar()

                if total_p > 0 and n > 0:
                    if i <= remainder:
                        s = (i - 1) * (base + 1) + 1
                        e = i * (base + 1)
                    else:
                        s = remainder * (base + 1) + (i - remainder - 1) * base + 1
                        e = s + base - 1
                    start_var.set(str(s) if s <= total_p else "")
                    end_var.set(str(e) if e <= total_p else "")

                tk.Entry(row_frame, textvariable=name_var, width=24,
                         relief="solid", bd=1, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=2)
                tk.Entry(row_frame, textvariable=start_var, width=7,
                         relief="solid", bd=1, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=2)
                tk.Entry(row_frame, textvariable=end_var, width=7,
                         relief="solid", bd=1, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=2)

                tk.Entry(row_frame, textvariable=path_var, width=44,
                         relief="solid", bd=1, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=2)

                self._make_btn(row_frame, "选择",
                               command=lambda v=path_var, nv=name_var: self.browse_save(v, nv),
                               font_size=8, padx=8, pady=2).pack(side="left", padx=2)

                self.split_entries.append({
                    "name": name_var,
                    "start": start_var,
                    "end": end_var,
                    "path": path_var
                })

                # 名称变更时自动同步保存路径
                name_var.trace_add("write", lambda *a, nv=name_var, pv=path_var:
                                   self._sync_path_on_name_change(nv, pv))

            self.status_label.config(text=f"已生成 {n} 个选项，可手动编辑或点击「AI自动识别扉页」", foreground="#333")

    def browse_save(self, path_var, name_var):
        default_name = name_var.get().strip() or "拆分文件"
        if not default_name.endswith(".pdf"):
            default_name += ".pdf"
        src_path = self.pdf_path.get()
        default_dir = os.path.dirname(src_path) if src_path else os.path.expanduser("~")

        full_path = filedialog.asksaveasfilename(
            title="保存为",
            initialdir=default_dir,
            initialfile=default_name,
            defaultextension=".pdf",
            filetypes=[("PDF 文件", "*.pdf")]
        )
        if full_path:
            path_var.set(full_path)

    # ==================== AI 自动分析扉页 ====================

    def auto_analyze(self):
        """AI 自动识别扉页并填入（自动检测扫描件，切换文字/图片模式）"""
        if not self.config.get("api_key"):
            ret = messagebox.askyesno("API 未配置", "尚未配置大模型 API，是否前往设置？")
            if ret:
                self.open_settings()
            return

        path = self.pdf_path.get()
        if not path or not os.path.isfile(path):
            messagebox.showwarning("提示", "请先选择一个有效的PDF文件")
            return

        self.status_label.config(text="正在分析PDF...", foreground="#0078D4")
        self.root.update()

        def _analyze():
            try:
                import fitz
                doc = fitz.open(path)
                total = len(doc)
                is_scanned = detect_scanned_pdf(doc)

                if is_scanned:
                    # ===== 扫描件：图片识别模式 =====
                    self.root.after(0, lambda: self.status_label.config(
                        text=f"检测为扫描件，切换图片识别模式（{total} 页）...",
                        foreground="#0078D4"))

                    # 渲染所有页面为图片
                    user_content = []
                    user_content.append({"type": "text", "text": f"这是一个共 {total} 页的PDF扫描件。以下是每页的截图，请识别哪些页面是扉页：\n"})

                    for i in range(total):
                        self.root.after(0, lambda v=i+1, t=total: self.status_label.config(
                            text=f"正在渲染页面 ({v}/{t})...", foreground="#0078D4"))
                        b64 = render_page_to_b64(doc[i], dpi=120, quality=50)
                        user_content.append({"type": "text", "text": f"\n--- 第 {i+1} 页 ---"})
                        user_content.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}
                        })

                    user_content.append({"type": "text", "text": "\n\n请输出识别结果JSON。"})

                    doc.close()

                    self.root.after(0, lambda: self.status_label.config(
                        text=f"正在调用 AI 图片分析（共 {total} 页）...", foreground="#0078D4"))

                    result = call_llm_api(self.config, SYSTEM_PROMPT_FEIYE_VISION, user_content)

                else:
                    # ===== 文字版：文本识别模式 =====
                    self.root.after(0, lambda: self.status_label.config(
                        text=f"正在提取 {total} 页完整文本...", foreground="#0078D4"))

                    page_texts = []
                    page_features = []
                    for i in range(total):
                        page = doc[i]
                        rect = page.rect
                        page_area = rect.width * rect.height
                        text = page.get_text().strip()
                        if not text:
                            text = "(此页无文字)"
                        page_texts.append(text)

                        blocks = page.get_text("blocks")
                        text_blocks = [b for b in blocks if b[6] == 0]
                        image_blocks = [b for b in blocks if b[6] == 1]

                        text_area = 0
                        centered = False
                        mid_x = rect.width / 2
                        for b in text_blocks:
                            bw = b[2] - b[0]
                            bh = b[3] - b[1]
                            text_area += bw * bh
                            if abs((b[0] + b[2]) / 2 - mid_x) / rect.width < 0.2:
                                centered = True

                        text_ratio = (text_area / page_area * 100) if page_area > 0 else 0
                        images_on_page = page.get_images()

                        page_features.append({
                            "page_number": i + 1,
                            "char_count": len(text),
                            "block_count": len(text_blocks),
                            "text_area_ratio": round(text_ratio, 1),
                            "image_count": max(len(image_blocks), len(images_on_page)),
                            "centered": centered,
                        })

                    doc.close()

                    parts = []
                    parts.append("## 第一步：请通读以下PDF每页的文本内容\n")
                    for i, txt in enumerate(page_texts):
                        display = txt if len(txt) <= 500 else txt[:500] + "..."
                        parts.append(f"\n=== 第 {i+1} 页 ===\n{display}")

                    parts.append("\n\n## 第二步：以下是每页的布局指标，用于辅助验证你的判断\n")
                    for pf in page_features:
                        ct = "是" if pf["centered"] else "否"
                        parts.append(
                            f"第{pf['page_number']}页: 字数={pf['char_count']} | "
                            f"块数={pf['block_count']} | 文字面积={pf['text_area_ratio']}% | "
                            f"图片={pf['image_count']} | 居中={ct}"
                        )

                    parts.append("\n\n## 第三步：请判断哪些页面是扉页\n")
                    parts.append("请基于你对全文的理解和上面的布局指标，按 system prompt 中的扉页特征进行判断。输出JSON格式。")

                    self.root.after(0, lambda: self.status_label.config(
                        text=f"正在调用 AI 分析（共 {total} 页）...", foreground="#0078D4"))

                    result = call_llm_api(self.config, SYSTEM_PROMPT_FEIYE, "\n".join(parts))

                # === 共同：处理识别结果 ===
                title_pages = result.get("title_pages", [])
                if not title_pages:
                    self.root.after(0, lambda: messagebox.showinfo(
                        "未识别到扉页",
                        "AI 未能在当前PDF中找到扉页。\n\n"
                        "可能原因：\n"
                        "1. PDF是扫描件但模型不支持图片识别\n"
                        "2. 文档结构不包含明显的扉页\n\n"
                        "您可以尝试「手动指定扉页页码」方式。"
                    ))
                    self.root.after(0, lambda: self.status_label.config(
                        text="AI 未识别到扉页", foreground="#CC6600"))
                    return

                print(f"\n=== AI 扉页识别结果 ===")
                for tp in title_pages:
                    print(f"  页 {tp.get('page_number')}: {tp.get('title')}")
                print("========================")

                self.root.after(0, lambda d=result: self.generate_entries(title_data=d))

            except Exception as e:
                self.root.after(0, lambda err=str(e): messagebox.showerror("AI 分析失败", err))
                self.root.after(0, lambda: self.status_label.config(
                    text="❌ AI 分析失败", foreground="#CC0000"))

        threading.Thread(target=_analyze, daemon=True).start()

    def manual_title_pages(self):
        """手动指定扉页页码，直接按页码范围切分，不调用AI"""
        path = self.pdf_path.get()
        if not path or not os.path.isfile(path):
            messagebox.showwarning("提示", "请先选择一个有效的PDF文件")
            return

        raw = self.manual_pages_var.get().strip()
        if not raw:
            messagebox.showwarning("提示", "请输入扉页页码，如 1,15,30,40,47")
            return

        # 解析页码
        try:
            page_nums = []
            for part in raw.replace("，", ",").split(","):
                p = int(part.strip())
                page_nums.append(p)
        except ValueError:
            messagebox.showerror("格式错误", "页码必须为数字，请使用逗号分隔")
            return

        # 去重并排序
        page_nums = sorted(set(page_nums))

        # 验证范围
        try:
            total = int(self.total_pages.get())
        except Exception:
            total = 0

        if total <= 0:
            messagebox.showerror("错误", "请先加载PDF获取总页数")
            return

        invalid = [p for p in page_nums if p < 1 or p > total]
        if invalid:
            messagebox.showerror("页码超出范围",
                                 f"以下页码无效：{invalid}\nPDF总共 {total} 页")
            return

        if not page_nums:
            return

        # 直接用页码构建拆分数据，不调用AI
        title_pages = [{"page_number": pn, "title": f"第{pn}页起"}
                       for pn in page_nums]

        self.status_label.config(
            text=f"已按 {len(page_nums)} 个指定页码生成拆分选项",
            fg=self.COLORS["success"])
        
        self.generate_entries(title_data={"title_pages": title_pages})

    # ==================== 拆分执行 ====================

    def set_unified_path(self):
        """为所有拆分文件设置统一的保存目录，文件名自动取自名称栏"""
        if not self.split_entries:
            messagebox.showwarning("提示", "请先生成拆分选项")
            return

        folder = filedialog.askdirectory(title="选择统一保存目录")
        if not folder:
            return

        count = 0
        for entry in self.split_entries:
            name = entry["name"].get().strip()
            if not name:
                continue
            if not name.endswith(".pdf"):
                name += ".pdf"
            entry["path"].set(os.path.join(folder, name))
            count += 1

        self.status_label.config(
            text=f"✅ 已为 {count} 个文件设置统一保存路径：{folder}",
            foreground="#00A000")

    def _sync_path_on_name_change(self, name_var, path_var, *_):
        """当文件名修改后，自动更新保存路径中的文件名部分"""
        old_path = path_var.get().strip()
        if not old_path:
            return  # 还没有设置过路径，不需要同步

        new_name = name_var.get().strip()
        if not new_name:
            return

        if not new_name.endswith(".pdf"):
            new_name += ".pdf"

        # 用新文件名替换旧路径的最后一段
        folder = os.path.dirname(old_path)
        new_path = os.path.join(folder, new_name)
        path_var.set(new_path)

    def start_split(self):
        pdf_path = self.pdf_path.get()
        if not pdf_path:
            messagebox.showwarning("提示", "请先选择一个PDF文件")
            return
        if not os.path.isfile(pdf_path):
            messagebox.showerror("错误", "PDF文件不存在")
            return
        if not self.split_entries:
            messagebox.showwarning("提示", "请先生成拆分选项")
            return

        # 验证输入
        for i, entry in enumerate(self.split_entries):
            name = entry["name"].get().strip()
            s = entry["start"].get().strip()
            e = entry["end"].get().strip()
            p = entry["path"].get().strip()

            if not name:
                messagebox.showerror("输入错误", f"第 {i+1} 行：文件名称不能为空")
                return
            if not s or not e:
                messagebox.showerror("输入错误", f"第 {i+1} 行：起始页码和结束页码不能为空")
                return
            if not p:
                messagebox.showerror("输入错误", f"第 {i+1} 行：请选择保存路径")
                return
            try:
                sp = int(s)
                ep = int(e)
            except ValueError:
                messagebox.showerror("输入错误", f"第 {i+1} 行：页码必须为数字")
                return
            if sp < 1 or ep < 1 or sp > ep:
                messagebox.showerror("输入错误", f"第 {i+1} 行：页码范围无效（{sp}-{ep}）")
                return

            # 路径冲突检测
            for j in range(i):
                if entry["path"].get().strip() == self.split_entries[j]["path"].get().strip():
                    if not messagebox.askyesno("路径冲突",
                                               f"第 {i+1} 行与第 {j+1} 行保存路径相同，是否继续？"):
                        return
                    break

        self.progress_bar["value"] = 0
        self.progress_bar["maximum"] = len(self.split_entries)
        self.status_label.config(text="正在拆分...", foreground="#0078D4")

        def _split():
            try:
                import fitz
                src_doc = fitz.open(pdf_path)
                total = len(src_doc)

                for idx, entry in enumerate(self.split_entries):
                    name = entry["name"].get().strip()
                    sp = int(entry["start"].get().strip()) - 1
                    ep = int(entry["end"].get().strip()) - 1
                    save_path = entry["path"].get().strip()

                    if sp < 0 or ep >= total or sp > ep:
                        self.root.after(0, lambda i=idx+1:
                            messagebox.showerror("拆分错误", f"第 {i} 个文件页码范围超出文档范围"))
                        src_doc.close()
                        return

                    new_doc = fitz.open()
                    new_doc.insert_pdf(src_doc, from_page=sp, to_page=ep)
                    new_doc.save(save_path, garbage=4, deflate=True)
                    new_doc.close()

                    self.root.after(0, lambda v=idx+1: self.progress_bar.configure(value=v))
                    self.root.after(0, lambda n=name: self.status_label.config(
                        text=f"已拆分：{n}", foreground="#333"))

                src_doc.close()

                self.root.after(0, lambda: self.progress_bar.configure(value=0))
                self.root.after(0, lambda: self.status_label.config(
                    text="✅ 拆分完成！", foreground="#00A000"))
                self.root.after(0, lambda: messagebox.showinfo(
                    "完成",
                    f"PDF拆分完成！共拆分为 {len(self.split_entries)} 个文件。"
                ))

            except Exception as e:
                self.root.after(0, lambda err=str(e): messagebox.showerror("拆分失败", err))
                self.root.after(0, lambda: self.status_label.config(
                    text="❌ 拆分失败", foreground="#CC0000"))
                self.root.after(0, lambda: self.progress_bar.configure(value=0))

        threading.Thread(target=_split, daemon=True).start()

    def clear_all(self):
        if messagebox.askyesno("确认清空", "确定要清空所有内容吗？"):
            self.pdf_path.set("")
            self.total_pages.set("未加载")
            self.split_count.set(2)
            self._page_texts_cache = []
            if hasattr(self, "placeholder"):
                if self.placeholder.winfo_exists():
                    self.placeholder.destroy()
            for widget in self.scrollable_frame.winfo_children():
                widget.destroy()
            self.split_entries.clear()
            self.placeholder = tk.Label(
                self.scrollable_frame,
                text="加载 PDF 后点击「AI 自动识别」或「手动指定」开始",
                fg=self.COLORS["text_light"], bg=self.COLORS["surface"],
                font=("Microsoft YaHei UI", 11)
            )
            self.placeholder.pack(pady=60)
            self.status_label.config(text="已清空", fg=self.COLORS["text_light"])
            self.progress_bar["value"] = 0


def main():
    root = tk.Tk()
    app = PDFSplitterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
