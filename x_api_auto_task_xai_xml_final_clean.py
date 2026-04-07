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
    
    # 构建用来避免和 Markdown 代码块解析器冲突的标志符
    code_block_fence = "`" * 3
    
    for attempt in range(1, 4):
        try:
            chat = client.chat.create(model="grok-4.20-0309-reasoning")
            chat.append(system("You are a professional analytical bot. You strictly output in XML format as instructed."))
            chat.append(user(prompt))
            result = chat.sample().content.strip()
            result = re.sub(r"<think>.*?</think>", "", result, flags=re.S | re.I).strip()
            
            # 使用字符串拼接的方式，避免当前文件被截断
            result = re.sub(r"^" + code_block_fence + r"(?:xml)?\s*", "", result, flags=re.M)
            result = re.sub(code_block_fence + r"$", "", result, flags=re.M).strip()
            
            print(f"✅ [xAI] 返回成功，长度 {len(result)}", flush=True)
            return result
        except Exception as e:
            print(f"⚠️ [xAI] 第 {attempt} 次失败: {e}", flush=True)
            time.sleep(2 * attempt)
    return ""

def parse_llm_xml(xml_text: str) -> dict:
    data = {
        "cover": {"title": "", "prompt": "", "insight": ""},
        "pulse": "",
        "themes": [],
        "top_picks": [],
    }
    if not xml_text:
        return data

    # ── COVER ──────────
    cover_match = re.search(
        r'<COVER\s+title=[\'"\u201c\u201d](.*?)[\'"\u201c\u201d]\s+prompt=[\'"\u201c\u201d](.*?)[\'"\u201c\u201d]\s+insight=[\'"\u201c\u201d](.*?)[\'"\u201c\u201d]\s*/?>',
        xml_text, re.I | re.S)
    if cover_match:
        data["cover"] = {
            "title":   norm_text(cover_match.group(1)),
            "prompt":  norm_text(cover_match.group(2)),
            "insight": norm_text(cover_match.group(3)),
        }
    else:
        cm = re.search(r'<COVER>(.*?)</COVER>', xml_text, re.I | re.S)
        if cm:
            body = cm.group(1)
            def _ct(tag):
                m = re.search(rf'<{tag}>(.*?)</{tag}>', body, re.I | re.S)
                return norm_text(m.group(1)) if m else ""
            data["cover"] = {"title": _ct("title"), "prompt": _ct("prompt"), "insight": _ct("insight")}

    # ── PULSE ──────────────────────────────────────────────────────────────
    pm = re.search(r'<PULSE>(.*?)</PULSE>', xml_text, re.I | re.S)
    if pm:
        data["pulse"] = norm_text(pm.group(1))

    # ── THEMES ─────────────────────────────────────────────────────────────
    for tm in re.finditer(r'<THEME([^>]*)>(.*?)</THEME>', xml_text, re.I | re.S):
        attr_str  = tm.group(1)
        theme_body = tm.group(2)

        type_m  = re.search(r'type\s*=\s*[\'"\u201c\u201d](.*?)[\'"\u201c\u201d]', attr_str, re.I)
        emoji_m = re.search(r'emoji\s*=\s*[\'"\u201c\u201d](.*?)[\'"\u201c\u201d]', attr_str, re.I)
        theme_type = norm_text(type_m.group(1)).lower()  if type_m  else "shift"
        emoji      = norm_text(emoji_m.group(1))          if emoji_m else "🧠"

        def _g(tag):
            m = re.search(rf'<{tag}>(.*?)</{tag}>', theme_body, re.I | re.S)
            return norm_text(m.group(1)) if m else ""

        tweets = []
        for tw in re.finditer(r'<TWEET\b([^>]*)>(.*?)</TWEET>', theme_body, re.I | re.S):
            raw_attrs = tw.group(1)
            content   = norm_text(tw.group(2))
            acc_m  = re.search(r'account\s*=\s*[\'"\u201c\u201d](.*?)[\'"\u201c\u201d]', raw_attrs, re.I)
            role_m = re.search(r'role\s*=\s*[\'"\u201c\u201d](.*?)[\'"\u201c\u201d]', raw_attrs, re.I)
            lk_m   = re.search(r'likes\s*=\s*[\'"\u201c\u201d](.*?)[\'"\u201c\u201d]', raw_attrs, re.I)
            rp_m   = re.search(r'replies\s*=\s*[\'"\u201c\u201d](.*?)[\'"\u201c\u201d]', raw_attrs, re.I)
            tweets.append({
                "account": norm_text(acc_m.group(1))  if acc_m  else "",
                "role":    norm_text(role_m.group(1)) if role_m else "",
                "likes":   safe_int(lk_m.group(1))   if lk_m   else 0,
                "replies": safe_int(rp_m.group(1))   if rp_m   else 0,
                "content": content,
            })

        data["themes"].append({
            "type":       theme_type,
            "emoji":      emoji,
            "title":      _g("TITLE"),
            "narrative":  _g("NARRATIVE"),
            "tweets":     tweets,
            "consensus":  _g("CONSENSUS"),
            "divergence": _g("DIVERGENCE"),
            "outlook":    _g("OUTLOOK"),
            "opportunity":_g("OPPORTUNITY"),
            "risk":       _g("RISK"),
        })

    # ── TOP_PICKS / TOPPICKS ───────────────────────────────────────────────
    picks_m = re.search(r'<(?:TOP_PICKS|TOPPICKS)>(.*?)</(?:TOP_PICKS|TOPPICKS)>', xml_text, re.I | re.S)
    if picks_m:
        for tw in re.finditer(r'<TWEET\b([^>]*)>(.*?)</TWEET>', picks_m.group(1), re.I | re.S):
            raw_attrs = tw.group(1)
            content   = norm_text(tw.group(2))
            acc_m  = re.search(r'account\s*=\s*[\'"\u201c\u201d](.*?)[\'"\u201c\u201d]', raw_attrs, re.I)
            role_m = re.search(r'role\s*=\s*[\'"\u201c\u201d](.*?)[\'"\u201c\u201d]', raw_attrs, re.I)
            lk_m   = re.search(r'likes\s*=\s*[\'"\u201c\u201d](.*?)[\'"\u201c\u201d]', raw_attrs, re.I)
            rp_m   = re.search(r'replies\s*=\s*[\'"\u201c\u201d](.*?)[\'"\u201c\u201d]', raw_attrs, re.I)
            data["top_picks"].append({
                "account": norm_text(acc_m.group(1))  if acc_m  else "",
                "role":    norm_text(role_m.group(1)) if role_m else "",
                "likes":   safe_int(lk_m.group(1))   if lk_m   else 0,
                "replies": safe_int(rp_m.group(1))   if rp_m   else 0,
                "content": content,
            })

    return data

def xml_escape(s: str) -> str:
    s = str(s or "")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def build_report_xml(parsed_data: dict) -> str:
    lines = ["<REPORT>"]
    cover = parsed_data.get("cover", {}) or {}
    lines.append("<COVER>")
    lines.append(f"<title>{xml_escape(cover.get('title', ''))}</title>")
    lines.append(f"<prompt>{xml_escape(cover.get('prompt', ''))}</prompt>")
    lines.append(f"<insight>{xml_escape(cover.get('insight', ''))}</insight>")
    lines.append("</COVER>")
    lines.append(f"<PULSE>{xml_escape(parsed_data.get('pulse', ''))}</PULSE>")
    lines.append("<THEMES>")
    for theme in parsed_data.get("themes", []):
        lines.append(f'<THEME type="{xml_escape(theme.get("type","shift"))}" emoji="{xml_escape(theme.get("emoji","🧠"))}">')
        lines.append(f"<TITLE>{xml_escape(theme.get('title',''))}</TITLE>")
        lines.append(f"<NARRATIVE>{xml_escape(theme.get('narrative',''))}</NARRATIVE>")
        for t in theme.get("tweets", []):
            lines.append(
                f'<TWEET account="{xml_escape(t.get("account",""))}" role="{xml_escape(t.get("role",""))}" likes="{safe_int(t.get("likes",0))}" replies="{safe_int(t.get("replies",0))}">{xml_escape(t.get("content",""))}</TWEET>'
            )
        for tag, key in [("CONSENSUS","consensus"),("DIVERGENCE","divergence"),
                         ("OUTLOOK","outlook"),("OPPORTUNITY","opportunity"),("RISK","risk")]:
            if theme.get(key):
                lines.append(f"<{tag}>{xml_escape(theme[key])}</{tag}>")
        lines.append("</THEME>")
    lines.append("</THEMES>")
    lines.append("<TOPPICKS>")
    for t in parsed_data.get("top_picks", []):
        lines.append(
            f'<TWEET account="{xml_escape(t.get("account",""))}" role="{xml_escape(t.get("role",""))}" likes="{safe_int(t.get("likes",0))}" replies="{safe_int(t.get("replies",0))}">{xml_escape(t.get("content",""))}</TWEET>'
        )
    lines.append("</TOPPICKS>")
    lines.append("</REPORT>")
    return "\n".join(lines)

def render_feishu_card(parsed_data: dict, today_str: str):
    webhooks = get_feishu_webhooks()
    if not webhooks or not parsed_data.get("pulse"):
        return
    
    # 1. 独立加粗的今日看板板块
    elements = [
        {"tag": "markdown", "content": "**今日看板 (The Pulse)**"},
        {"tag": "markdown", "content": f"<font color='grey'>{parsed_data['pulse']}</font>"},
        {"tag": "hr"},
    ]
    
    # 2. 独立加粗的深度叙事板块
    if parsed_data.get("themes"):
        elements.append({"tag": "markdown", "content": "**深度叙事追踪（Thematic Narratives）**"})
        
        themes_list = parsed_data.get("themes", [])
        for idx, theme in enumerate(themes_list):
            prefix = "🆕 新叙事" if theme.get("type") == "new" else "🔁 旧共识迁移"
            # 标题强制加粗
            md = f"**{theme.get('emoji','🧠')} {theme.get('title','')}**\n<font color='grey'>{prefix}｜{theme.get('narrative','')}</font>\n"
            for t in theme.get("tweets", []):
                md += "\n" + render_quote_tweet_markdown(t) + "\n"
            if theme.get("type") == "new":
                if theme.get("outlook"):     md += f"\n<font color='blue'>🔮 解读与展望：{theme['outlook']}</font>"
                if theme.get("opportunity"): md += f"\n<font color='green'>🎯 潜在机会：{theme['opportunity']}</font>"
                if theme.get("risk"):        md += f"\n<font color='red'>⚠️ 潜在风险：{theme['risk']}</font>"
            else:
                if theme.get("consensus"):  md += f"\n<font color='green'>🔥 核心共识：{theme['consensus']}</font>"
                if theme.get("divergence"): md += f"\n<font color='red'>⚔️ 最大分歧：{theme['divergence']}</font>"
            
            elements.append({"tag": "markdown", "content": md.strip()})
            
            # 每个子话题之间注入飞书专属视觉分割线（横线）
            if idx < len(themes_list) - 1:
                elements.append({"tag": "hr"})

    # 3. 独立加粗的今日精选推文板块
    if parsed_data.get("top_picks"):
        elements.append({"tag": "hr"})
        c = "**今日精选推文 (Top 5 Picks)**\n\n"
        for t in parsed_data["top_picks"][:5]:
            c += render_quote_tweet_markdown(t) + "\n\n"
        elements.append({"tag": "markdown", "content": c.strip()})
        
    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True, "enable_forward": True},
            "header": {"title": {"tag": "plain_text", "content": f"昨晚硅谷在聊啥｜{today_str}"}, "template": "blue"},
            "elements": elements,
        },
    }
    for url in webhooks:
        try:
            resp = requests.post(url, json=payload, timeout=20)
            if resp.status_code == 200:
                print("✅ [飞书] 推送成功", flush=True)
            else:
                print(f"⚠️ [飞书] 推送失败 {resp.status_code}: {resp.text[:200]}", flush=True)
        except Exception as e:
            print(f"⚠️ [飞书] 推送异常: {e}", flush=True)

def render_wechat_html(parsed_data: dict, cover_url: str = "") -> str:
    lines = []
    if cover_url:
        lines.append(f'<p style="text-align:center;margin:0 0 16px 0;"><img src="{html_escape_text(cover_url)}" style="max-width:100%;border-radius:8px;"/></p>')
    
    # 1. 独立加粗的今日看板板块
    lines.append('<h2 style="margin:0 0 12px 0;"><strong>今日看板 (The Pulse)</strong></h2>')
    lines.append(f'<blockquote style="background:#f8f9fa;border-left:4px solid #8c98a4;padding:10px 14px;color:#555;">{html_escape_text(parsed_data.get("pulse",""))}</blockquote>')
    
    # 2. 独立加粗的深度叙事板块
    themes_list = parsed_data.get("themes", [])
    if themes_list:
        lines.append('<h2 style="margin:24px 0 12px 0;"><strong>深度叙事追踪（Thematic Narratives）</strong></h2>')
        for idx, theme in enumerate(themes_list):
            # 标题强制加粗
            lines.append(f'<h3 style="margin:16px 0 8px 0;"><strong>{html_escape_text(theme.get("emoji","🧠"))} {html_escape_text(theme.get("title",""))}</strong></h3>')
            lines.append(f'<div style="background:#f4f8fb;padding:10px 12px;border-radius:6px;margin:0 0 8px 0;">{html_escape_text(theme.get("narrative",""))}</div>')
            for t in theme.get("tweets", []):
                lines.append(render_quote_tweet_html(t))
            if theme.get("type") == "new":
                if theme.get("outlook"):     lines.append(f'<p><strong>🔮 解读与展望：</strong>{html_escape_text(theme["outlook"])}</p>')
                if theme.get("opportunity"): lines.append(f'<p><strong>🎯 潜在机会：</strong>{html_escape_text(theme["opportunity"])}</p>')
                if theme.get("risk"):        lines.append(f'<p><strong>⚠️ 潜在风险：</strong>{html_escape_text(theme["risk"])}</p>')
            else:
                if theme.get("consensus"):  lines.append(f'<p><strong>🔥 核心共识：</strong>{html_escape_text(theme["consensus"])}</p>')
                if theme.get("divergence"): lines.append(f'<p><strong>⚔️ 最大分歧：</strong>{html_escape_text(theme["divergence"])}</p>')
            
            # 每个子话题之间注入微信专属视觉分割线（虚线）
            if idx < len(themes_list) - 1:
                lines.append('<hr style="border:0;border-top:1px dashed #ccc;margin:24px 0;">')

    # 3. 独立加粗的今日精选推文板块
    if parsed_data.get("top_picks"):
        lines.append('<h2 style="margin:24px 0 12px 0;"><strong>今日精选推文 (Top 5 Picks)</strong></h2>')
        for t in parsed_data["top_picks"][:5]:
            lines.append(render_quote_tweet_html(t))
            
    return "".join(lines)

def generate_cover_image(prompt: str) -> str:
    if not SF_API_KEY or not prompt:
        return ""
    try:
        resp = requests.post(
            URL_SF_IMAGE,
            headers={"Authorization": f"Bearer {SF_API_KEY}", "Content-Type": "application/json"},
            json={"model": "Kwai-Kolors/Kolors", "prompt": prompt, "image_size": "1024x576"},
            timeout=60,
        )
        if resp.status_code == 200:
            body = resp.json()
            return body.get("images", [{}])[0].get("url") or body.get("data", [{}])[0].get("url") or ""
        print(f"⚠️ [SiliconFlow] 生图失败 {resp.status_code}: {resp.text[:200]}", flush=True)
    except Exception as e:
        print(f"⚠️ [SiliconFlow] 异常: {e}", flush=True)
    return ""

def upload_to_imgbb_via_url(sf_url: str) -> str:
    if not IMGBB_API_KEY or not sf_url:
        return sf_url
    try:
        img_b64 = base64.b64encode(requests.get(sf_url, timeout=30).content).decode("utf-8")
        resp = requests.post(URL_IMGBB, data={"key": IMGBB_API_KEY, "image": img_b64}, timeout=45)
        if resp.status_code == 200:
            return resp.json()["data"]["url"]
        print(f"⚠️ [ImgBB] 上传失败: {resp.text[:200]}", flush=True)
    except Exception as e:
        print(f"⚠️ [ImgBB] 异常: {e}", flush=True)
    return sf_url

def push_to_wechat(html_content: str, title: str, cover_url: str = ""):
    webhooks = get_wechat_webhooks()
    if not webhooks:
        return
    payload = {"title": title, "author": "Prinski", "html_content": html_content, "cover_jpg": cover_url}
    for url in webhooks:
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 200:
                print("✅ [微信/集简云] 推送成功", flush=True)
            else:
                print(f"⚠️ [微信/集简云] 推送失败 {resp.status_code}: {resp.text[:200]}", flush=True)
        except Exception as e:
            print(f"⚠️ [微信/集简云] 推送异常: {e}", flush=True)

def save_daily_data(today_str: str, post_objects: list, report_text: str, parsed_data: dict | None = None):
    data_dir = Path(f"data/{today_str}")
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "combined.txt").write_text("\n".join(json.dumps(obj, ensure_ascii=False) for obj in post_objects), encoding="utf-8")
    final_report = build_report_xml(parsed_data) if parsed_data else report_text
    if final_report:
        (data_dir / "daily_report.txt").write_text(final_report, encoding="utf-8")
        (data_dir / "raw_llm_report.xml").write_text(report_text or "", encoding="utf-8")
        (data_dir / "parsed_data.json").write_text(json.dumps(parsed_data or {}, ensure_ascii=False, indent=2), encoding="utf-8")

def _load_stats_file(path: Path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _update_stats_bucket(stats: dict, feed: list, used_accounts: set, today_str: str):
    touched_today = set()
    for t in feed:
        acc = (t.get("a", "") or "unknown").lower().replace("@", "")
        if acc not in stats:
            stats[acc] = {"fetched_days": 0, "total_tweets": 0, "used_in_reports": 0, "last_active": ""}
        if acc not in touched_today and stats[acc].get("last_active") != today_str:
            stats[acc]["fetched_days"] += 1
            touched_today.add(acc)
        stats[acc]["total_tweets"] += 1
        stats[acc]["last_active"] = today_str
    for acc in used_accounts:
        acc = (acc or "").lower().replace("@", "")
        if acc in stats:
            stats[acc]["used_in_reports"] += 1
    return stats

def update_account_stats(final_feed: list, parsed_data: dict):
    target_file = Path("data/target_accounts_stats.json")
    echo_file = Path("data/echo_accounts_stats.json")
    target_stats = _load_stats_file(target_file)
    echo_stats = _load_stats_file(echo_file)
    today_str = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    used_accounts = set()
    for theme in parsed_data.get("themes", []):
        for t in theme.get("tweets", []):
            used_accounts.add((t.get("account", "") or "").lower())
    for t in parsed_data.get("top_picks", []):
        used_accounts.add((t.get("account", "") or "").lower())
    target_feed = [x for x in final_feed if is_target_account(x.get("a", ""))]
    echo_feed = [x for x in final_feed if not is_target_account(x.get("a", ""))]
    target_stats = _update_stats_bucket(target_stats, target_feed, {a for a in used_accounts if is_target_account(a)}, today_str)
    echo_stats = _update_stats_bucket(echo_stats, echo_feed, {a for a in used_accounts if not is_target_account(a)}, today_str)
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(json.dumps(target_stats, ensure_ascii=False, indent=2), encoding="utf-8")
    echo_file.write_text(json.dumps(echo_stats, ensure_ascii=False, indent=2), encoding="utf-8")

def main():
    print("=" * 60, flush=True)
    print("昨晚硅谷在聊啥 v17.0 (深度整合版)", flush=True)
    print("=" * 60, flush=True)
    print(f"[模式] TEST_MODE={TESTMODE}", flush=True)

    if not TWITTERAPI_IO_KEY:
        print("❌ 错误: 未配置 TWITTERAPI_IO_KEY", flush=True)
        return
    if not TARGET_SET:
        print("❌ 错误: whales.txt / experts.txt 为空或未读取到", flush=True)
        return

    print(f"✅ [环境] TWITTERAPI_IO_KEY 已读取: {bool(TWITTERAPI_IO_KEY)}", flush=True)
    print(f"✅ [名单] WHALE={len(WHALE_ACCOUNTS)} | EXPERT={len(EXPERT_ACCOUNTS)} | TARGET={len(TARGET_SET)}", flush=True)

    today_str, _ = today_and_yesterday()
    raw_feed = []
    acc_list = list(TARGET_SET)
    batch_size = 10 if TESTMODE else 12

    for i in range(0, len(acc_list), batch_size):
        chunk = acc_list[i:i + batch_size]
        q1 = " OR ".join(f"from:{a}" for a in chunk) + f" since:{SINCE_DATE_STR} -filter:retweets"
        q2 = " OR ".join(chunk) + f" since:{SINCE_DATE_STR} min_faves:20 -filter:replies"
        originals = fetch_advanced_search_pages(q1, query_type="Latest", max_pages=2)
        echoes = fetch_advanced_search_pages(q2, query_type="Top", max_pages=2)
        raw_feed.extend(originals)
        raw_feed.extend(echoes)
        print(f"📦 [批次] {i // batch_size + 1}: originals={len(originals)} | echoes={len(echoes)}", flush=True)
        time.sleep(1.0)

    if not raw_feed:
        print("❌ 没抓到任何推文，流程结束", flush=True)
        return

    clean_feed = score_and_filter(raw_feed)
    if not clean_feed:
        print("❌ 经过打分过滤后没有可用推文", flush=True)
        return

    tier1 = clean_feed[:15]
    tier2 = clean_feed[15:REPORT_POOL_LIMIT]
    for t in tier1:
        replies = fetch_reply_pages(t["id"], max_pages=2)
        t["deep_replies"] = filter_deep_replies(replies)
        time.sleep(0.6)

    report_candidates = []
    for t in tier1:
        reply_strs = [f"@{r['author']}: {r['text'][:180]} (♥{r['likes']})" for r in t.get("deep_replies", [])]
        stext = t["text"] + ("\n\nDeep replies:\n" + "\n".join(reply_strs) if reply_strs else "")
        report_candidates.append({
            "a": t["author"],
            "tweetid": t["id"],
            "l": t["likes"],
            "r": t["replies"],
            "q": t["quotes"],
            "score": t["score"],
            "t": t["created_ts"],
            "source_type": t.get("source_type", "unknown"),
            "s": stext,
        })
    for t in tier2:
        report_candidates.append({
            "a": t["author"],
            "tweetid": t["id"],
            "l": t["likes"],
            "r": t["replies"],
            "q": t["quotes"],
            "score": t["score"],
            "t": t["created_ts"],
            "source_type": t.get("source_type", "unknown"),
            "s": t["text"],
        })

    combined_jsonl = "\n".join(json.dumps(obj, ensure_ascii=False) for obj in report_candidates)
    if not combined_jsonl.strip():
        print("❌ LLM 输入为空", flush=True)
        return

    memory = load_memory()
    today_accounts = {str(t.get("a", "")).lower() for t in report_candidates}
    memory_context_lines = []
    for acc in today_accounts:
        if acc in memory and memory[acc]:
            memory_context_lines.append(f"{acc} -> " + " | ".join(memory[acc]))
    memory_context = "\n".join(memory_context_lines)

    xml_result = llm_call_xai(combined_jsonl, today_str, memory_context)
    if not xml_result:
        print("❌ LLM 返回为空", flush=True)
        return

    parsed_data = parse_llm_xml(xml_result)
    parsed_data = postprocess_parsed_data_cn(parsed_data)

    memory_candidates = build_memory_candidates(parsed_data)
    update_character_memory(parsed_data, today_str)
    save_memory_snapshot(today_str, memory_candidates)

    cover_url = ""
    cover_prompt = (parsed_data.get("cover") or {}).get("prompt", "")
    if cover_prompt:
        sf_url = generate_cover_image(cover_prompt)
        cover_url = upload_to_imgbb_via_url(sf_url) if sf_url else ""

    render_feishu_card(parsed_data, today_str)
    wechat_hooks = get_wechat_webhooks()
    if wechat_hooks:
        html_content = render_wechat_html(parsed_data, cover_url)
        title = (parsed_data.get("cover") or {}).get("title", "昨晚硅谷在聊啥")
        push_to_wechat(html_content, title, cover_url=cover_url)

    save_daily_data(today_str, report_candidates, xml_result, parsed_data)
    update_account_stats(report_candidates, parsed_data)
    print("✅ v17.0 执行完成", flush=True)


if __name__ == "__main__":
    main()
