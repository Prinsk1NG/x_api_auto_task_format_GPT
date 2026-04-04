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
PPLX_API_KEY = os.getenv("PPLX_API_KEY", "").strip() or os.getenv("PERPLEXITY_API_KEY", "").strip()
SF_API_KEY = os.getenv("SF_API_KEY", "").strip()
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY", "").strip()

# backward-compatible aliases for older names inside historical code blocks
TWITTERAPIIOKEY = TWITTERAPI_IO_KEY
XAIAPIKEY = XAI_API_KEY
PPLXAPIKEY = PPLX_API_KEY
SFAPIKEY = SF_API_KEY
IMGBBAPIKEY = IMGBB_API_KEY

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
    author_obj = t.get("author", {})
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


def default_special_sections():
    return (
        [
            {"category": "一级市场", "content": "暂无新增高置信投资线索，继续观察本地AI代理、推理基础设施、多模态生成三条线。"},
            {"category": "二级市场", "content": "暂无新增高置信交易结论，维持对算力、推理成本、应用渗透率三项指标的跟踪。"},
        ],
        [
            {"category": "中国 AI 最新动态", "content": "暂无外部补充成功，建议继续跟踪中国模型发布、算力供给、监管口径与应用落地。"},
            {"category": "中美 AI 博弈与衍生风险", "content": "暂无外部补充成功，建议继续跟踪芯片限制、模型开源竞争、云服务与地缘监管外溢风险。"},
        ],
    )


def _extract_json_object(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
        return {}


def _normalize_special_section_items(items, fallback):
    result = []
    for item in items or []:
        category = norm_text((item or {}).get("category", ""))
        content = norm_text((item or {}).get("content", ""))
        if category and content:
            result.append({"category": category, "content": content})
    return result or fallback


def fetch_special_sections_with_perplexity(today_str: str):
    default_investment_radar, default_risk_china_view = default_special_sections()
    if not PPLX_API_KEY:
        print("⚠️ [Perplexity] 未设置 API Key，使用默认外部栏目结构。", flush=True)
        return default_investment_radar, default_risk_china_view
    prompt = f"""
今天是 {today_str}。你是AI行业观察员，请输出严格 JSON，不要 markdown，不要解释，不要代码块。
目标：补充日报中的两个栏目，只保留高信息密度、与AI行业直接相关的内容。
1) investment_radar：给出 2 条，类别只能是“一级市场”或“二级市场”。
2) risk_china_view：必须给出 2 条，类别固定为“中国 AI 最新动态”和“中美 AI 博弈与衍生风险”。
输出格式：
{{
  "investment_radar": [
    {{"category": "一级市场", "content": "..."}},
    {{"category": "二级市场", "content": "..."}}
  ],
  "risk_china_view": [
    {{"category": "中国 AI 最新动态", "content": "..."}},
    {{"category": "中美 AI 博弈与衍生风险", "content": "..."}}
  ]
}}
""".strip()
    try:
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {PPLX_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "sonar",
                "messages": [
                    {"role": "system", "content": "You are a concise research assistant that always returns valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
            },
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"⚠️ [Perplexity] 状态码 {resp.status_code}，使用默认结构。", flush=True)
            return default_investment_radar, default_risk_china_view
        data = resp.json()
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        parsed = _extract_json_object(content)
        investment_radar = _normalize_special_section_items(parsed.get("investment_radar"), default_investment_radar)
        risk_china_view = _normalize_special_section_items(parsed.get("risk_china_view"), default_risk_china_view)
        categories = {x["category"] for x in risk_china_view}
        if "中国 AI 最新动态" not in categories or "中美 AI 博弈与衍生风险" not in categories:
            risk_china_view = default_risk_china_view
        return investment_radar, risk_china_view
    except Exception as e:
        print(f"⚠️ [Perplexity] 请求失败，使用默认结构: {e}", flush=True)
        return default_investment_radar, default_risk_china_view


def build_xml_prompt(combined_jsonl: str, today_str: str, memory_context: str) -> str:
    return f"""
你是专业AI行业分析机器人。请基于给定的 X/Twitter 数据，严格输出 XML，不要 markdown，不要解释。

要求：
1. 输出 <REPORT> 根节点。
2. 生成 <COVER><title>...</title><prompt>...</prompt><insight>...</insight></COVER>
3. 生成 <PULSE> 一段总脉冲。
4. 生成 4-6 个 <THEME>，属性 type 只能是 shift 或 new，并带 emoji。
5. 每个 THEME 需包含 <TITLE>、<NARRATIVE>、若干 <TWEET account="..." role="...">...</TWEET>。
6. shift 类型必须尽量给出 <CONSENSUS> 和 <DIVERGENCE>。
7. new 类型必须尽量给出 <OUTLOOK>、<OPPORTUNITY>、<RISK>。
8. 生成 <TOPPICKS>，包含 5 条最值得读的推文。
9. 内容用中文输出，引用的账号保留英文 handle。

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
            chat = client.chat.create(model="grok-4.1")
            chat.append(system("You are a professional analytical bot. You strictly output in XML format as instructed."))
            chat.append(user(prompt))
            result = chat.sample().content.strip()
            result = re.sub(r"<think>.*?</think>", "", result, flags=re.S | re.I).strip()
            result = re.sub(r"^```(?:xml)?\s*", "", result, flags=re.M)
            result = re.sub(r"```$", "", result, flags=re.M).strip()
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
        "investment_radar": [],
        "risk_china_view": [],
        "top_picks": [],
    }
    if not xml_text:
        return data

    cover_match = re.search(r"<COVER>.*?<title>(.*?)</title>.*?<prompt>(.*?)</prompt>.*?<insight>(.*?)</insight>.*?</COVER>", xml_text, re.I | re.S)
    if cover_match:
        data["cover"] = {
            "title": norm_text(cover_match.group(1)),
            "prompt": norm_text(cover_match.group(2)),
            "insight": norm_text(cover_match.group(3)),
        }

    pulse_match = re.search(r"<PULSE>(.*?)</PULSE>", xml_text, re.I | re.S)
    if pulse_match:
        data["pulse"] = norm_text(pulse_match.group(1))

    for tm in re.finditer(r"<THEME\s+type=\"(.*?)\"\s+emoji=\"(.*?)\">(.*?)</THEME>", xml_text, re.I | re.S):
        theme_type, emoji, body = tm.groups()
        title = re.search(r"<TITLE>(.*?)</TITLE>", body, re.I | re.S)
        narrative = re.search(r"<NARRATIVE>(.*?)</NARRATIVE>", body, re.I | re.S)
        consensus = re.search(r"<CONSENSUS>(.*?)</CONSENSUS>", body, re.I | re.S)
        divergence = re.search(r"<DIVERGENCE>(.*?)</DIVERGENCE>", body, re.I | re.S)
        outlook = re.search(r"<OUTLOOK>(.*?)</OUTLOOK>", body, re.I | re.S)
        opportunity = re.search(r"<OPPORTUNITY>(.*?)</OPPORTUNITY>", body, re.I | re.S)
        risk = re.search(r"<RISK>(.*?)</RISK>", body, re.I | re.S)

        tweets = []
        for tw in re.finditer(r"<TWEET\s+account=\"(.*?)\"\s+role=\"(.*?)\">(.*?)</TWEET>", body, re.I | re.S):
            tweets.append({
                "account": norm_text(tw.group(1)),
                "role": norm_text(tw.group(2)),
                "content": norm_text(tw.group(3)),
            })

        data["themes"].append({
            "type": norm_text(theme_type).lower() or "shift",
            "emoji": norm_text(emoji) or "🧠",
            "title": norm_text(title.group(1) if title else ""),
            "narrative": norm_text(narrative.group(1) if narrative else ""),
            "tweets": tweets,
            "consensus": norm_text(consensus.group(1) if consensus else ""),
            "divergence": norm_text(divergence.group(1) if divergence else ""),
            "outlook": norm_text(outlook.group(1) if outlook else ""),
            "opportunity": norm_text(opportunity.group(1) if opportunity else ""),
            "risk": norm_text(risk.group(1) if risk else ""),
        })

    top_match = re.search(r"<TOPPICKS>(.*?)</TOPPICKS>", xml_text, re.I | re.S)
    if top_match:
        for tw in re.finditer(r"<TWEET\s+account=\"(.*?)\"\s+role=\"(.*?)\">(.*?)</TWEET>", top_match.group(1), re.I | re.S):
            data["top_picks"].append({
                "account": norm_text(tw.group(1)),
                "role": norm_text(tw.group(2)),
                "content": norm_text(tw.group(3)),
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
        lines.append(f'<THEME type="{xml_escape(theme.get("type", "shift"))}" emoji="{xml_escape(theme.get("emoji", "🧠"))}">')
        lines.append(f"<TITLE>{xml_escape(theme.get('title', ''))}</TITLE>")
        lines.append(f"<NARRATIVE>{xml_escape(theme.get('narrative', ''))}</NARRATIVE>")
        for t in theme.get("tweets", []):
            lines.append(
                f'<TWEET account="{xml_escape(t.get("account", ""))}" role="{xml_escape(t.get("role", ""))}">{xml_escape(t.get("content", ""))}</TWEET>'
            )
        if theme.get("consensus"):
            lines.append(f"<CONSENSUS>{xml_escape(theme.get('consensus', ''))}</CONSENSUS>")
        if theme.get("divergence"):
            lines.append(f"<DIVERGENCE>{xml_escape(theme.get('divergence', ''))}</DIVERGENCE>")
        if theme.get("outlook"):
            lines.append(f"<OUTLOOK>{xml_escape(theme.get('outlook', ''))}</OUTLOOK>")
        if theme.get("opportunity"):
            lines.append(f"<OPPORTUNITY>{xml_escape(theme.get('opportunity', ''))}</OPPORTUNITY>")
        if theme.get("risk"):
            lines.append(f"<RISK>{xml_escape(theme.get('risk', ''))}</RISK>")
        lines.append("</THEME>")
    lines.append("</THEMES>")
    lines.append("<INVESTMENTRADAR>")
    for item in parsed_data.get("investment_radar", []):
        lines.append(f'<ITEM category="{xml_escape(item.get("category", ""))}">{xml_escape(item.get("content", ""))}</ITEM>')
    lines.append("</INVESTMENTRADAR>")
    lines.append("<RISKCHINAVIEW>")
    for item in parsed_data.get("risk_china_view", []):
        lines.append(f'<ITEM category="{xml_escape(item.get("category", ""))}">{xml_escape(item.get("content", ""))}</ITEM>')
    lines.append("</RISKCHINAVIEW>")
    lines.append("<TOPPICKS>")
    for t in parsed_data.get("top_picks", []):
        lines.append(
            f'<TWEET account="{xml_escape(t.get("account", ""))}" role="{xml_escape(t.get("role", ""))}">{xml_escape(t.get("content", ""))}</TWEET>'
        )
    lines.append("</TOPPICKS>")
    lines.append("</REPORT>")
    return "\n".join(lines)


def render_feishu_card(parsed_data: dict, today_str: str):
    webhooks = get_feishu_webhooks()
    if not webhooks or not parsed_data.get("pulse"):
        return
    elements = [
        {"tag": "markdown", "content": f"**The Pulse**\n<font color='grey'>{parsed_data['pulse']}</font>"},
        {"tag": "hr"},
    ]
    for idx, theme in enumerate(parsed_data.get("themes", [])):
        prefix = "🆕 新叙事" if theme.get("type") == "new" else "🔁 旧共识迁移"
        theme_md = f"**{theme.get('emoji','🧠')} {theme.get('title','')}**\n<font color='grey'>{prefix}｜{theme.get('narrative','')}</font>\n"
        for t in theme.get("tweets", []):
            theme_md += f"\n- **@{t.get('account','')}** | {t.get('role','')}\n> {t.get('content','')}\n"
        if theme.get("type") == "new":
            if theme.get("outlook"):
                theme_md += f"\n<font color='blue'>🔮 解读与展望：{theme['outlook']}</font>"
            if theme.get("opportunity"):
                theme_md += f"\n<font color='green'>🎯 潜在机会：{theme['opportunity']}</font>"
            if theme.get("risk"):
                theme_md += f"\n<font color='red'>⚠️ 潜在风险：{theme['risk']}</font>"
        else:
            if theme.get("consensus"):
                theme_md += f"\n<font color='green'>🔥 核心共识：{theme['consensus']}</font>"
            if theme.get("divergence"):
                theme_md += f"\n<font color='red'>⚔️ 最大分歧：{theme['divergence']}</font>"
        elements.append({"tag": "markdown", "content": theme_md.strip()})
        if idx < len(parsed_data.get("themes", [])) - 1:
            elements.append({"tag": "hr"})
    if parsed_data.get("investment_radar"):
        elements.append({"tag": "hr"})
        content = "**💰 Investment Radar**\n"
        for item in parsed_data["investment_radar"]:
            content += f"\n- **{item['category']}**：{item['content']}"
        elements.append({"tag": "markdown", "content": content})
    if parsed_data.get("risk_china_view"):
        elements.append({"tag": "hr"})
        content = "**🌏 China / Risk View**\n"
        for item in parsed_data["risk_china_view"]:
            content += f"\n- **{item['category']}**：{item['content']}"
        elements.append({"tag": "markdown", "content": content})
    if parsed_data.get("top_picks"):
        elements.append({"tag": "hr"})
        content = "**⭐ Top Picks**\n"
        for t in parsed_data["top_picks"][:5]:
            content += f"\n- **@{t['account']}** | {t['role']}\n> {t['content']}"
        elements.append({"tag": "markdown", "content": content})

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
        lines.append(f'<p style="text-align:center;margin:0 0 16px 0;"><img src="{cover_url}" style="max-width:100%;border-radius:8px;"/></p>')
    lines.append('<h2 style="margin:0 0 12px 0;">The Pulse</h2>')
    lines.append(f'<blockquote style="background:#f8f9fa;border-left:4px solid #8c98a4;padding:10px 14px;color:#555;">{parsed_data.get("pulse", "")}</blockquote>')
    lines.append('<h2 style="margin:24px 0 12px 0;">主题脉络</h2>')
    for theme in parsed_data.get("themes", []):
        lines.append(f'<h3 style="margin:16px 0 8px 0;">{theme.get("emoji","🧠")} {theme.get("title","")}</h3>')
        lines.append(f'<div style="background:#f4f8fb;padding:10px 12px;border-radius:6px;margin:0 0 8px 0;">{theme.get("narrative","")}</div>')
        for t in theme.get("tweets", []):
            lines.append(f'<p><strong>@{t.get("account","")}</strong> <span style="color:#94a3b8;">| {t.get("role","")}</span></p>')
            lines.append(f'<blockquote style="background:#f8f9fa;border-left:4px solid #8c98a4;padding:10px 14px;color:#555;">{t.get("content","")}</blockquote>')
    if parsed_data.get("investment_radar"):
        lines.append('<h2 style="margin:24px 0 12px 0;">Investment Radar</h2>')
        for item in parsed_data["investment_radar"]:
            lines.append(f'<p><strong>{item["category"]}</strong>：{item["content"]}</p>')
    if parsed_data.get("risk_china_view"):
        lines.append('<h2 style="margin:24px 0 12px 0;">China / Risk View</h2>')
        for item in parsed_data["risk_china_view"]:
            lines.append(f'<p><strong>{item["category"]}</strong>：{item["content"]}</p>')
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
    print("昨晚硅谷在聊啥 v16.1 (稳定修正版)", flush=True)
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
    default_investment_radar, default_risk_china_view = default_special_sections()
    parsed_data["investment_radar"] = default_investment_radar
    parsed_data["risk_china_view"] = default_risk_china_view

    investment_radar, risk_china_view = fetch_special_sections_with_perplexity(today_str)
    if investment_radar:
        parsed_data["investment_radar"] = investment_radar
    if risk_china_view:
        parsed_data["risk_china_view"] = risk_china_view

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
    print("✅ v16.1 执行完成", flush=True)


if __name__ == "__main__":
    main()
