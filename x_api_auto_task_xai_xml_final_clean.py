# -*- coding: utf-8 -*-
import os
import re
import json
import time
import base64
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests

try:
    from xai_sdk import Client
    from xai_sdk.chat import user, system
except Exception as e:
    raise RuntimeError(
        "xai-sdk 未正确安装。请确认 workflow 已执行: pip install --no-cache-dir xai-sdk"
    ) from e

# ==============================================================================
# ENV
# ==============================================================================
TESTMODE = os.getenv("TEST_MODE_ENV", "false").lower() == "true"

FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
FEISHU_WEBHOOK_URL_1 = os.getenv("FEISHU_WEBHOOK_URL_1", "").strip()
WECHAT_WEBHOOK_URL = os.getenv("WECHAT_WEBHOOK_URL", "").strip()
WECHAT_WEBHOOK_URL_1 = os.getenv("WECHAT_WEBHOOK_URL_1", "").strip()
JIJYUN_WEBHOOK_URL = os.getenv("JIJYUN_WEBHOOK_URL", "").strip()
ORISG_WEBHOOK_URL = os.getenv("ORISG_WEBHOOK_URL", "").strip()
ORICN_WEBHOOK_URL = os.getenv("ORICN_WEBHOOK_URL", "").strip()

TWITTERAPI_IO_KEY = os.getenv("TWITTERAPI_IO_KEY", "").strip()
XAI_API_KEY = os.getenv("XAI_API_KEY", "").strip()
SF_API_KEY = os.getenv("SF_API_KEY", "").strip()
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY", "").strip()

# ==============================================================================
# CONSTANTS
# ==============================================================================
BASE_URL = "https://api.twitterapi.io"
URL_SF_IMAGE = "https://api.siliconflow.cn/v1/images/generations"
URL_IMGBB = "https://api.imgbb.com/1/upload"
BJ_TZ = timezone(timedelta(hours=8))
NOW_UTC = datetime.now(timezone.utc)
SINCE_24H = NOW_UTC - timedelta(days=1)
SINCE_TS = int(SINCE_24H.timestamp())
SINCE_DATE_STR = SINCE_24H.strftime("%Y-%m-%d")

MIN_REPLY_LIKES = 8
MIN_REPLY_LEN = 24
MAX_DEEP_REPLIES_PER_TWEET = 3
REPORT_POOL_LIMIT = 75
MEMORY_POOL_LIMIT = 20
CHARACTER_MEMORY_MAX_PER_ACCOUNT = 5

AI_CORE_KEYWORDS = {
    "ai", "agent", "agents", "model", "models", "llm", "grok", "gemma", "gemini",
    "claude", "openai", "anthropic", "xai", "nvidia", "huggingface", "inference",
    "reasoning", "token", "tokens", "multimodal", "gpu", "chips", "chip", "robot",
    "robots", "coding", "codegen", "rag", "retrieval", "codex", "benchmark", "training"
}
NON_AI_HOT_NOISE = {
    "tesla", "fsd", "spacex", "starlink", "trump", "election", "ukraine",
    "immigration", "border", "tariff"
}
TOXIC_PATTERNS = [
    r"\bpedo\b", r"\bidiot\b", r"\bstupid\b", r"\bfuck\b", r"\bwtf\b",
    r"\bscam\b", r"\bracist\b", r"\btrash\b", r"\bgarbage\b"
]

ROLE_MAP = {
    "product expert": "产品专家",
    "product manager": "产品专家",
    "pm": "产品专家",
    "builder": "产品/增长操盘手",
    "founder": "创始人",
    "cofounder": "联合创始人",
    "investor": "投资人",
    "vc": "投资人",
    "researcher": "研究员",
    "scientist": "研究员",
    "engineer": "工程师",
    "developer": "工程师",
    "analyst": "分析师",
    "writer": "作者",
    "journalist": "媒体人",
    "media": "媒体人",
}

# ==============================================================================
# HELPERS
# ==============================================================================
def today_and_yesterday():
    now = datetime.now(BJ_TZ)
    yesterday = now - timedelta(days=1)
    return now.strftime("%Y-%m-%d"), yesterday.strftime("%Y-%m-%d")

def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()

def safe_int(v):
    try:
        return int(float(v or 0))
    except Exception:
        return 0

def normalize_role_cn(role: str) -> str:
    role = norm_text(role)
    if not role:
        return "行业观察者"
    low = role.lower()
    for k, v in ROLE_MAP.items():
        if k in low:
            return v
    if any(x in low for x in ["产品", "product", "pm"]):
        return "产品专家"
    if any(x in low for x in ["投资", "invest", "vc"]):
        return "投资人"
    if any(x in low for x in ["研究", "research", "scientist"]):
        return "研究员"
    if any(x in low for x in ["工程", "engineer", "developer", "dev"]):
        return "工程师"
    if any(x in low for x in ["创始", "founder"]):
        return "创始人"
    if any(x in low for x in ["媒体", "journalist", "media"]):
        return "媒体人"
    return role if len(role) <= 12 else "行业观察者"

def looks_mostly_english(text: str) -> bool:
    text = norm_text(text)
    if not text:
        return False
    en = len(re.findall(r"[A-Za-z]", text))
    zh = len(re.findall("[\u4e00-\u9fff]", text))
    return en > 24 and en > zh * 2

def soft_translate_tweet_to_cn(text: str) -> str:
    text = norm_text(text)
    if not text:
        return ""
    replacements = [
        ("Software isn't precious anymore", "软件不再是稀缺资源"),
        ("High quality software is infinitely available", "高质量软件正在变得近乎无限供给"),
        ("Things that used to be hard suddenly become very easy", "很多过去很难的事情，现在突然变得很容易"),
        ("AI agents are going to need money", "AI agents 很快会需要自己的钱"),
        ("The grand unification of AI and crypto is about to happen", "AI 与 crypto 的大融合正在发生"),
        ("What you can do with Grok Imagine", "Grok Imagine 能做到的事情"),
        ("Grok is constantly being updated", "Grok 一直去持续更新"),
        ("Tesla cars, especially with FSD, are the safest in the world",
         "Tesla 汽车，尤其是搭载 FSD 时，是世界上最安全的汽车"),
        ("Using voice for Imagine is a great feature for young kids",
         "对还不会写复杂 prompt 的孩子来说，用语音驱动 Imagine 是个很棒的功能"),
        ("Grok can help you come up with great prompts for images and videos",
         "Grok 可以帮你想出更好的图片和视频 prompts"),
        ("It changed the game by being the best car, period",
         "它靠\"就是最好的车\"这一点直接改写了行业格局"),
    ]
    out = text
    for s, d in replacements:
        out = out.replace(s, d)
    if looks_mostly_english(out):
        out = out.replace(" and ", "，").replace(" but ", "，但").replace(" because ", "，因为")
        out = out.replace(" with ", "，带着").replace(" is ", " 是 ").replace(" are ", " 是 ")
        out = out.replace("very easy", "非常容易").replace("image generation", "图像生成")
        out = out.replace("creative control", "创作控制力").replace("programming language", "编程语言")
    return out.strip(' \u201c\u201d"')

def finalize_cn_tweet_text(text: str) -> str:
    text = soft_translate_tweet_to_cn(text)
    text = text.replace("..", "。")
    text = re.sub(r"\s+([，。！？；：])", r"\1", text)
    text = re.sub(r"([，。！？；：]){2,}", r"\1", text)
    return text.strip(' \u201c\u201d"')

def compress_pulse_text(text: str, themes: list | None = None) -> str:
    text = norm_text(text)
    titles = [norm_text((x or {}).get("title", "")) for x in (themes or [])
              if norm_text((x or {}).get("title", ""))]
    if not text:
        if len(titles) >= 2:
            return f"重点关注：{titles[0]}、{titles[1]}。"[:60]
        if len(titles) == 1:
            return f"重点关注：{titles[0]}。"[:60]
        return ""
    text = text.replace("The Pulse", "").replace("今日看板：", "").replace("今日看板", "").strip("：: ")
    if len(text) <= 60:
        return text
    if len(titles) >= 2:
        for c in [f"重点关注：{titles[0]}、{titles[1]}。", f"今日聚焦「{titles[0]}」与「{titles[1]}」。"]:
            if len(c) <= 60:
                return c
    if len(titles) == 1:
        c = f"重点关注：{titles[0]}。"
        if len(c) <= 60:
            return c
    text = re.split(r"[。；;!！?？]", text)[0].strip()
    return (text[:60].rstrip("，、；： ") + "。") if len(text) > 60 else text

def metric_suffix(likes=0, replies=0) -> str:
    likes, replies = safe_int(likes), safe_int(replies)
    if likes <= 0 and replies <= 0:
        return ""
    return f"（❤️ {likes} | 💬 {replies}）"

def render_quote_tweet_markdown(t: dict) -> str:
    account = norm_text((t or {}).get("account", "")).replace("@", "")
    role = normalize_role_cn((t or {}).get("role", ""))
    content = finalize_cn_tweet_text((t or {}).get("content", ""))
    suffix = metric_suffix((t or {}).get("likes", 0), (t or {}).get("replies", 0))
    if suffix:
        content = f"{content}{suffix}"
    return f'🗣️ @{account} | {role}\n"{content}"'

def html_escape_text(s: str) -> str:
    s = str(s or "")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def render_quote_tweet_html(t: dict) -> str:
    account = norm_text((t or {}).get("account", "")).replace("@", "")
    role = normalize_role_cn((t or {}).get("role", ""))
    content = finalize_cn_tweet_text((t or {}).get("content", ""))
    suffix = metric_suffix((t or {}).get("likes", 0), (t or {}).get("replies", 0))
    if suffix:
        content = f"{content}{suffix}"
    return (
        f'<p><strong>🗣️ @{html_escape_text(account)} | {html_escape_text(role)}</strong></p>'
        f'<blockquote style="background:#f8f9fa;border-left:4px solid #8c98a4;'
        f'padding:10px 14px;color:#555;">"{html_escape_text(content)}"</blockquote>'
    )

def postprocess_parsed_data_cn(parsed_data: dict) -> dict:
    parsed_data["pulse"] = compress_pulse_text(
        parsed_data.get("pulse", ""), parsed_data.get("themes", [])
    )
    for theme in parsed_data.get("themes", []) or []:
        for key in ("title", "narrative", "consensus", "divergence", "outlook", "opportunity", "risk"):
            theme[key] = norm_text(theme.get(key, ""))
        for t in theme.get("tweets", []) or []:
            t["role"] = normalize_role_cn(t.get("role", ""))
            t["content"] = finalize_cn_tweet_text(t.get("content", ""))
            t["likes"] = safe_int(t.get("likes", 0))
            t["replies"] = safe_int(t.get("replies", 0))
    for t in parsed_data.get("top_picks", []) or []:
        t["role"] = normalize_role_cn(t.get("role", ""))
        t["content"] = finalize_cn_tweet_text(t.get("content", ""))
        t["likes"] = safe_int(t.get("likes", 0))
        t["replies"] = safe_int(t.get("replies", 0))
    return parsed_data

def load_account_list(filename: str):
    path = Path(filename)
    if not path.exists():
        print(f"⚠️ [名单] 未找到文件: {filename}", flush=True)
        return []
    items = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        items.append(line.replace("@", "").lower())
    print(f"✅ [名单] {filename}: {len(items)} 个账号", flush=True)
    return items

WHALE_ACCOUNTS = load_account_list("whales.txt")
EXPERT_ACCOUNTS = load_account_list("experts.txt")
if TESTMODE:
    WHALE_ACCOUNTS = WHALE_ACCOUNTS[:2]
    EXPERT_ACCOUNTS = EXPERT_ACCOUNTS[:4]
TARGET_SET = set(WHALE_ACCOUNTS + EXPERT_ACCOUNTS)

def is_target_account(acc: str) -> bool:
    return (acc or "").replace("@", "").lower() in TARGET_SET

def get_feishu_webhooks():
    urls = []
    if TESTMODE:
        if FEISHU_WEBHOOK_URL:
            urls.append(FEISHU_WEBHOOK_URL)
    else:
        if FEISHU_WEBHOOK_URL_1:
            urls.append(FEISHU_WEBHOOK_URL_1)
    return urls

def get_wechat_webhooks():
    urls = []
    if TESTMODE:
        if WECHAT_WEBHOOK_URL:
            urls.append(WECHAT_WEBHOOK_URL)
    else:
        for url in [WECHAT_WEBHOOK_URL_1, JIJYUN_WEBHOOK_URL, ORISG_WEBHOOK_URL, ORICN_WEBHOOK_URL]:
            if url:
                urls.append(url)
    return urls

def _twitter_headers():
    return {"X-API-Key": TWITTERAPI_IO_KEY}

def unify_tweet_schema(t: dict):
    author_obj = t.get("author") or {}
    if isinstance(author_obj, str):
        author_handle = author_obj
    else:
        author_handle = author_obj.get("userName") or author_obj.get("screen_name") or author_obj.get("username") or "unknown"
    author_handle = author_handle.replace("@", "").strip().lower()

    created_at = t.get("createdAt") or t.get("created_at") or t.get("createdat") or ""
    created_ts = 0
    if created_at:
        try:
            created_ts = int(datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp())
        except Exception:
            try:
                created_ts = int(datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y").timestamp())
            except Exception:
                created_ts = 0

    return {
        "id": str(t.get("id") or t.get("tweetId") or t.get("rest_id") or "").strip(),
        "text": norm_text(t.get("text") or t.get("full_text") or t.get("fullText") or ""),
        "author": author_handle,
        "created_ts": created_ts,
        "likes": safe_int(t.get("likeCount") or t.get("favorite_count") or t.get("favoriteCount")),
        "replies": safe_int(t.get("replyCount") or t.get("reply_count")),
        "quotes": safe_int(t.get("quoteCount") or t.get("quote_count")),
        "deep_replies": [],
    }

def fetch_advanced_search_pages(query: str, query_type: str = "Latest", max_pages: int = 2):
    if not TWITTERAPI_IO_KEY:
        return []
    results = []
    seen_ids = set()
    cursor = None
    for _ in range(max_pages):
        params = {"query": query, "queryType": query_type}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = requests.get(
                f"{BASE_URL}/twitter/tweet/advanced_search",
                headers=_twitter_headers(),
                params=params,
                timeout=25,
            )
            if resp.status_code != 200:
                print(f"⚠️ [TwitterAPI] advanced_search HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
                break
            data = resp.json() or {}
            tweets = data.get("tweets") or []
            if not tweets:
                break
            for t in tweets:
                ct = unify_tweet_schema(t)
                if not ct["id"] or ct["id"] in seen_ids:
                    continue
                seen_ids.add(ct["id"])
                if ct["created_ts"] >= SINCE_TS:
                    results.append(ct)
            cursor = data.get("nextCursor") or data.get("next_cursor")
            if not (data.get("hasNextPage") or data.get("has_next_page")) or not cursor:
                break
            time.sleep(0.8)
        except Exception as e:
            print(f"⚠️ [TwitterAPI] advanced_search 异常: {e}", flush=True)
            break
    return results

def fetch_reply_pages(tweet_id: str, max_pages: int = 2):
    if not TWITTERAPI_IO_KEY or not tweet_id:
        return []
    reply_map = {}
    cursor = None
    for _ in range(max_pages):
        params = {"tweetId": tweet_id}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = requests.get(
                f"{BASE_URL}/twitter/tweet/replies",
                headers=_twitter_headers(),
                params=params,
                timeout=20,
            )
            if resp.status_code != 200:
                break
            data = resp.json() or {}
            tweets = data.get("tweets") or []
            if not tweets:
                break
            for r in tweets:
                rr = unify_tweet_schema(r)
                if rr["id"] and rr["id"] not in reply_map:
                    reply_map[rr["id"]] = rr
            cursor = data.get("nextCursor") or data.get("next_cursor")
            if not (data.get("hasNextPage") or data.get("has_next_page")) or not cursor:
                break
            time.sleep(0.6)
        except Exception:
            break
    return sorted(reply_map.values(), key=lambda x: (x["likes"], x["replies"]), reverse=True)

def contains_ai_signal(text: str) -> bool:
    low = norm_text(text).lower()
    return any(k in low for k in AI_CORE_KEYWORDS)

def non_ai_noise_hits(text: str) -> int:
    low = norm_text(text).lower()
    return sum(1 for k in NON_AI_HOT_NOISE if k in low)

def looks_toxic_or_empty(text: str) -> bool:
    t = norm_text(text)
    if len(t) < MIN_REPLY_LEN:
        return True
    if re.fullmatch(r"[@\w\s\.\!\?\,\-:;]+", t) and len(t.split()) <= 4:
        return True
    low = t.lower()
    return any(re.search(p, low) for p in TOXIC_PATTERNS)

def filter_deep_replies(replies: list):
    clean = []
    seen = set()
    for r in replies or []:
        text = norm_text(r.get("text", ""))
        likes = safe_int(r.get("likes", 0))
        author = (r.get("author", "") or "").lower()
        if likes < MIN_REPLY_LIKES:
            continue
        if looks_toxic_or_empty(text):
            continue
        key = (author, text[:160].lower())
        if key in seen:
            continue
        seen.add(key)
        clean.append({"author": r.get("author", ""), "likes": likes, "text": text})
    clean.sort(key=lambda x: (x["likes"], len(x["text"])), reverse=True)
    return clean[:MAX_DEEP_REPLIES_PER_TWEET]

def apply_ai_relevance(post: dict) -> float:
    text = norm_text(post.get("text", ""))
    author = (post.get("author", "") or "").lower()
    bonus = 0.0
    if contains_ai_signal(text):
        bonus += 180.0
    if author in {"xai", "openai", "anthropicai", "googleai", "huggingface", "nvidia", "a16z", "pmarca"}:
        bonus += 80.0
    penalty = non_ai_noise_hits(text) * 120.0
    if ("tesla" in text.lower() or "fsd" in text.lower()) and not contains_ai_signal(text):
        penalty += 250.0
    return bonus - penalty

def score_and_filter(posts: list):
    seen = set()
    cleaned = []
    for t in posts or []:
        tid = str(t.get("id", "")).strip()
        text = norm_text(t.get("text", ""))
        if not tid or not text or tid in seen:
            continue
        seen.add(tid)
        likes = safe_int(t.get("likes", 0))
        replies = safe_int(t.get("replies", 0))
        quotes = safe_int(t.get("quotes", 0))
        base_score = likes + replies * 2 + quotes * 3
        total_score = base_score + apply_ai_relevance(t)
        if total_score < 300:
            continue
        t["score"] = round(total_score, 2)
        t["source_type"] = "target" if is_target_account(t.get("author", "")) else "echo"
        cleaned.append(t)
    cleaned.sort(key=lambda x: x.get("score", 0), reverse=True)
    return cleaned

def load_memory():
    memory_file = Path("data/character_memory.json")
    if not memory_file.exists():
        return {}
    try:
        return json.loads(memory_file.read_text(encoding="utf-8"))
    except Exception:
        return {}

def update_character_memory(parsed_data: dict, today_str: str):
    memory_file = Path("data/character_memory.json")
    memory = load_memory()
    new_items = {}
    for theme in parsed_data.get("themes", []):
        title = norm_text(theme.get("title", ""))
        for t in theme.get("tweets", []):
            acc = (t.get("account", "") or "").replace("@", "").lower()
            content = norm_text(t.get("content", ""))
            if acc and content:
                new_items.setdefault(acc, []).append(f"[{today_str}] {title}: {content}")
    for t in parsed_data.get("top_picks", []):
        acc = (t.get("account", "") or "").replace("@", "").lower()
        content = norm_text(t.get("content", ""))
        if acc and content:
            new_items.setdefault(acc, []).append(f"[{today_str}] TOP_PICK: {content}")
    for acc, items in new_items.items():
        existing = memory.get(acc, [])
        merged = []
        seen = set()
        for x in items + existing:
            if x not in seen:
                seen.add(x)
                merged.append(x)
        memory[acc] = merged[:CHARACTER_MEMORY_MAX_PER_ACCOUNT]
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    memory_file.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")

def build_memory_candidates(parsed_data: dict):
    candidates = []
    for theme in parsed_data.get("themes", []):
        title = norm_text(theme.get("title", ""))
        consensus = norm_text(theme.get("consensus", ""))
        divergence = norm_text(theme.get("divergence", ""))
        for t in theme.get("tweets", []):
            account = (t.get("account", "") or "").replace("@", "").lower()
            content = norm_text(t.get("content", ""))
            if account and content:
                candidates.append({
                    "account": account,
                    "summary": content,
                    "theme_title": title,
                    "consensus": consensus,
                    "divergence": divergence,
                })
        if divergence:
            candidates.append({
                "account": "_theme_divergence",
                "summary": divergence,
                "theme_title": title,
                "consensus": consensus,
                "divergence": divergence,
            })
    for t in parsed_data.get("top_picks", []):
        account = (t.get("account", "") or "").replace("@", "").lower()
        content = norm_text(t.get("content", ""))
        if account and content:
            candidates.append({
                "account": account,
                "summary": content,
                "theme_title": "TOP_PICK",
                "consensus": "",
                "divergence": "",
            })
    seen = set()
    uniq = []
    for item in candidates:
        key = (item["account"], item["summary"][:180])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq[:MEMORY_POOL_LIMIT]

def save_memory_snapshot(today_str: str, memory_candidates: list):
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / f"memory_{today_str}.json").write_text(
        json.dumps(memory_candidates, ensure_ascii=False, indent=2), encoding="utf-8"
    )

def build_xml_prompt(combined_jsonl: str, today_str: str, memory_context: str) -> str:
    return f"""
你是专业AI行业分析机器人。请基于给定的 X/Twitter 数据，严格输出 XML，不要 markdown，不要解释。

要求：
1. 输出 <REPORT> 根节点。
2. 生成 <COVER><title>...</title><prompt>...</prompt><insight>...</insight></COVER>。
3. 生成 <PULSE>，必须是中文，长度不超过60个汉字，只总结当天最值得关注的2个主题，不允许超过两个主题。
4. 生成 4-6 个 <THEME>，属性 type 只能是 shift 或 new，并带 emoji。
5. **专题监测拦截**：
   - 如果输入数据中包含明确的"投融资快讯"、"VC观点"、"资本市场"相关推文与讨论，请务必将其单独整合为一个 THEME，且 <TITLE> 必须精确写为「资本与估值雷达 (Investment Radar)」。
   - 如果输入数据中包含明确的"地缘与监管"、"中国 AI 评价"、"大国博弈"相关推文与讨论，请务必将其单独整合为另一个 THEME，且 <TITLE> 必须精确写为「风险与中国视角 (Risk & China View)」。
   - 如果没有上述两类内容的切实推文，请**不要硬说生造**这两个板块，直接呈现其他维度的深度叙事即可。
6. 每个 THEME 需包含 <TITLE>、<NARRATIVE>、若干 <TWEET account="..." role="..." likes="..." replies="...">...</TWEET>。
7. shift 类型必须尽量给出 <CONSENSUS> 和 <DIVERGENCE>。
8. new 类型必须尽量给出 <OUTLOOK>、<OPPORTUNITY>、<RISK>。
9. 生成 <TOPPICKS>，包含5条最值得读的推文，TWEET 格式同上。
10. 所有推文正文、主题里的引用推文、TOPPICKS 默认翻译成自然中文；但人名、账号名、品牌名、产品名、模型名、缩写、黑话、梗和必要英文术语不要硬翻。
11. role 必须是简短中文角色标签，例如：产品专家、投资人、研究员、创始人、工程师、媒体人。
12. 不要把账号、角色、点赞、评论写进正文内容里，这些信息只放在 TWEET 属性中。
13. 输入 JSONL 中 a=账号，l=点赞，r=评论，s=推文正文/上下文，请优先复用对应数据。

今天日期：{today_str}

历史记忆（可选）：
{memory_context or '无'}

输入数据(JSONL)：
{combined_jsonl}
""".strip()

def llm_call_xai(combined_jsonl: str, today_str: str, memory_context: str) -> str:
    if not XAI_API_KEY:
        print("❌ [xAI] 未配置 XAI_API_KEY", flush=True)
        return ""
    prompt = build_xml_prompt(combined_jsonl[:100000], today_str, memory_context)
    client = Client(api_key=XAI_API_KEY)
    for attempt in range(1, 4):
        try:
            chat = client.chat.create(model="grok-4.20-0309-reasoning")
            chat.append(system("You are a professional analytical bot. You strictly output in XML format as instructed."))
            chat.append(user(prompt))
            result = chat.sample().content.strip()
            result = re.sub(r"<think>.*?</think>", "", result, flags=re.S | re.I).strip()
            result = re.sub(r"^
