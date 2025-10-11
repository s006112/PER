import re
import unicodedata


# === NULL / URL / MAILTO 清理 ===
# === 字元修復表（Unicode & Windows-1252）===
CHAR_REPLACEMENTS = (
    # Windows-1252 residues kept at the top for clarity
    ("\x00", " "), ("\xa0", " "), 
    ("\x91", "'"), ("\x92", "'"),
    ("\x93", '"'), ("\x94", '"'),
    ("\x96", "-"), ("\x97", "-"),
    ("\x85", "..."), ("\x80", "€"),
    ("\x99", "™"),

    # Unicode variants and compatibility glyphs unified after normalization
    ("“", '"'), ("”", '"'),
    ("‘", "'"), ("’", "'"),
    ("–", "-"), ("—", "-"), ("―", "-"), ("‒", "-"), ("﹣", "-"), ("－", "-"),
    ("…", "..."), ("⋯", "..."),
    ("•", "•"), ("·", "•"), ("・", "•"), ("‧", "•"),
    ("（", "("), ("）", ")"), ("［", "["), ("］", "]"), ("｛", "{"), ("｝", "}"),
    ("＼", "\\"),
    ("\ufffd", " "),    # replacement character placeholder 
)

# === 通用格式清洗規則 ===
CLEAN_REGEXES_GENERAL = [
    (re.compile(r'(?:\b(?:nul|null)\b[\s/\\]*)+', flags=re.IGNORECASE), " "),
    # 表格 & 格式符號污染清理
    (re.compile(r"\|{2,}"), "|"),                         # 多個連續的 |
    (re.compile(r"(?:\|\s*){2,}"), " "),                  # 多欄位空值合併
    (re.compile(r"^\|\s*$", flags=re.MULTILINE), ""),     # 單獨佔行的 |

    # 常見符號重複壓縮
    (re.compile(r" {2,}"), " "),                          # 多空格
    (re.compile(r"\n{2,}"), "\n\n"),                      # 多換行
    (re.compile(r"-{2,}"), "-"),
    (re.compile(r"\+{2,}"), "+"),
    (re.compile(r"\.{2}"), "."),
    (re.compile(r"_+"), "_"),
#    (re.compile(r"\*{2,}"), ""),     
    # Aggressive                    
    (re.compile(r"\*+"), ""),                          # * 壓縮
    (re.compile(r"\>+"), ""),
    (re.compile(r"\<+"), ""),
    (re.compile(r"\<+"), ""),

    # 字元正規化
    (re.compile(r"[ \u3000]{2,}"), " "),                  # 中文全形空格壓縮
    (re.compile(r"[・‧]{2,}"), "・"),
    (re.compile(r"ͺ{2,}"), "ͺ"),

    # Email 引用符號清理
    (re.compile(r"(?:>\s*){2,}"), ">"),               # 多個連續的 >（含空格）保留單一 >

    # 標點後接英文單字無空格（如 Mr.Smith）
    (re.compile(r"(\w)([.!?])([A-Z])"), r"\1\2 \3"),

    # Invisible control character 清理
    (re.compile(r"[\u200b-\u200f\u202a-\u202e]"), ""),  # Invisible control characters

    # 中文破折號/分隔線異體統一（常見於 OCR）
    (re.compile(r"[─━―]+"), "-"), 

    # URL / MAILTO 清理
    (re.compile(r'https?://\S+', flags=re.IGNORECASE), " "),
]

_EMAIL_TOKEN_SPLITTER = re.compile(r'(\s+)')
_EMAIL_STRIP_CHARS = ".,;:!?()[]<>\"'"


def _remove_email_like_phrases(text: str) -> str:
    parts = _EMAIL_TOKEN_SPLITTER.split(text)
    for idx, part in enumerate(parts):
        if not part or part.isspace():
            continue
        token = part.strip(_EMAIL_STRIP_CHARS)
        if not token:
            continue
        lowered = token.lower()
        if '@' in token and 'com' in lowered:
            parts[idx] = ' '
    return ''.join(parts)


def sanitize_text(text: str | bytes) -> str:
    """Return clean Unicode text after removing corruption patterns and legacy encodings."""

    if not text:
        return ""
    if isinstance(text, bytes):
        for enc in ("utf-8", "windows-1252", "iso-8859-1"):
            try:
                text = text.decode(enc)
                break
            except Exception:
                continue
        else:
            text = text.decode("utf-8", errors="replace")

    # === Unicode 正規化 & 控制符號替換 ===
    text = unicodedata.normalize("NFKC", text)

    # === 字元處理（正規化後）===
    for bad, good in CHAR_REPLACEMENTS:
        text = text.replace(bad, good)

    text = _remove_email_like_phrases(text)

    # === 通用格式清洗（正則套件）===
    for regex, repl in CLEAN_REGEXES_GENERAL:
        text = regex.sub(repl, text)

    text = _remove_email_like_phrases(text)

    # === 結尾清理 ===
    return re.sub(r"\s+", " ", text).strip()
