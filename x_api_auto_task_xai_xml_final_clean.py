# -*- coding: utf-8 -*-
"""
x_api_auto_task_xai_xml.py  v15.0 (Twitter主线 + Perplexity定向补充版)
Architecture: TwitterAPI.io -> xAI SDK (Reasoning) + Memory Bank -> Perplexity (专项栏目)
"""

import os
import traceback
import uuid
import zipfile
import re
import json
import time
import base64
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from xai_sdk import Client
from xai_sdk.chat import user, system

TEST_MODE = os.getenv("TEST_MODE_ENV", "false").lower() == "true"

# ── 环境变量配置 ──────────────────────────────
SF_API_KEY = os.getenv("SF_API_KEY", "")
XAI_API_KEY = os.getenv("XAI_API_KEY", "")
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY", "")
PPLX_API_KEY = os.getenv("PPLX_API_KEY", "")
TWITTERAPI_IO_KEY = os.getenv("twitterapi_io_KEY", "")


def D(b64_str):
    return base64.b64decode(b64_str).decode("utf-8")


URL_SF_IMAGE = D("aHR0cHM6Ly9hcGkuc2lsaWNvbmZsb3cuY24vdjEvaW1hZ2VzL2dlbmVyYXRpb25z")
URL_IMGBB = D("aHR0cHM6Ly9hcGkuaW1nYmIuY29tLzEvdXBsb2Fk")

# ── 基础配置与时间窗 ──────────────────────────────
BASE_URL = "https://api.twitterapi.io"
NOW_UTC = datetime.now(timezone.utc)
SINCE_24H = NOW_UTC - timedelta(days=1)
SINCE_TS = int(SINCE_24H.timestamp())
SINCE_DATE_STR = SINCE_24H.strftime("%Y-%m-%d")


# 🚨 动态读取外部名单系统

def load_account_list(filename):
    if not os.path.exists(filename):
        return []
    with open(filename, "r", encoding="utf-8") as f:
        return [line.strip().replace("@", "").lower() for line in f if line.strip() and not line.strip().startswith("#")]


WHALE_ACCOUNTS = load_account_list("whales.txt")
EXPERT_ACCOUNTS = load_account_list("experts.txt")

if TEST_MODE:
    WHALE_ACCOUNTS = WHALE_ACCOUNTS[:2]
    EXPERT_ACCOUNTS = EXPERT_ACCOUNTS[:4]

TARGET_SET = set(WHALE_ACCOUNTS + EXPERT_ACCOUNTS)


# ── 渠道分发逻辑 ──────────────────────────────

def get_feishu_webhooks() -> list:
    urls = []
    if TEST_MODE:
        url = os.getenv("FEISHU_WEBHOOK_URL", "")
        if url:
            urls.append(url)
    else:
        for suffix in ["", "_1", "_2", "_3"]:
            url = os.getenv(f"FEISHU_WEBHOOK_URL{suffix}", "")
            if url:
                urls.append(url)
    return urls



def get_wechat_webhooks() -> list:
    urls = []
    for key in ["JIJYUN_WEBHOOK_URL", "OriSG_WEBHOOK_URL", "OriCN_WEBHOOK_URL"]:
        url = os.getenv(key, "").strip()
        if url and not url.startswith("#"):
            urls.append(url)
    return urls



def get_dates() -> tuple:
    tz = timezone(timedelta(hours=8))
    today = datetime.now(tz)
    yesterday = today - timedelta(days=1)
    return today.strftime("%Y-%m-%d"), yesterday.strftime("%Y-%m-%d")


# ==============================================================================
# 🎯 数据清洗与打分引擎
# ==============================================================================
AI_KEYWORDS = [
    "ai", "llm", "agent", "model", "gpt", "release", "inference",
    "open-source", "agi", "claude", "openai", "anthropic", "deepseek"
]


def unify_schema(t):
    author_obj = t.get("author", {})
    if isinstance(author_obj, str):
        author_handle = author_obj
    else:
        author_handle = author_obj.get("userName", "unknown")
    author_handle = author_handle.replace("@", "").strip().lower()

    created_at = t.get("createdAt", t.get("created_at", ""))
    created_ts = 0
    if created_at:
        try:
            created_ts = int(datetime.fromisoformat(created_at.replace('Z', '+00:00')).timestamp())
        except Exception:
            try:
                created_ts = int(datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y").timestamp())
            except Exception:
                print(f"⚠️ [日期解析失败] 碰到未知时间格式，该推文可能会被丢弃: {created_at}", flush=True)

    return {
        "id": str(t.get("id", t.get("tweet_id", "None"))),
        "text": t.get("text", t.get("full_text", "")),
        "author": author_handle,
        "created_ts": created_ts,
        "likes": int(t.get("likeCount", t.get("favorite_count", 0) or 0)),
        "replies": int(t.get("replyCount", t.get("reply_count", 0) or 0)),
        "quotes": int(t.get("quoteCount", t.get("quote_count", 0) or 0)),
        "deep_replies": []
    }



def score_and_filter(tweets):
    unique_tweets = {}
    for t in tweets:
        t_id = t.get("id")
        if not t_id or t_id == "None":
            continue
        if t_id in unique_tweets:
            continue

        score = t["likes"] * 1.0 + t["replies"] * 2.0 + t["quotes"] * 3.0
        text_lower = t["text"].lower()

        if t["author"] in WHALE_ACCOUNTS:
            score += 500
        elif t["author"] in EXPERT_ACCOUNTS:
            score += 50

        if any(kw in text_lower for kw in AI_KEYWORDS):
            score += 300

        clean_text = re.sub(r'https?://\S+|@\w+', '', text_lower).strip()
        if len(clean_text) < 15:
            score -= 500
        if t["text"].count('@') > 5:
            score -= 1000

        t["score"] = max(0, score)
        if t["score"] > 0 or t["likes"] > 15:
            unique_tweets[t_id] = t

    scored_list = sorted(unique_tweets.values(), key=lambda x: x["score"], reverse=True)
    author_counts = {}
    final_capped = []
    for t in scored_list:
        if author_counts.get(t["author"], 0) < 3:
            final_capped.append(t)
            author_counts[t["author"]] = author_counts.get(t["author"], 0) + 1
    return final_capped



def fetch_advanced_search_pages(query: str, query_type: str = "Latest", max_pages: int = 2) -> list:
    results = []
    cursor = None
    seen_ids = set()

    for _ in range(max_pages):
        params = {"query": query, "queryType": query_type}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = requests.get(
                f"{BASE_URL}/twitter/tweet/advanced_search",
                headers={"X-API-Key": TWITTERAPI_IO_KEY},
                params=params,
                timeout=25,
            )
            if resp.status_code != 200:
                print(f"❌ [TwitterAPI 报错] 搜索 HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
                break
            data = resp.json() or {}
            tweets = data.get("tweets") or []
            if not tweets:
                break
            for t in tweets:
                ct = unify_schema(t)
                if ct["id"] in seen_ids:
                    continue
                seen_ids.add(ct["id"])
                if ct["created_ts"] >= SINCE_TS:
                    results.append(ct)
            cursor = data.get("next_cursor")
            if not data.get("has_next_page") or not cursor:
                break
            time.sleep(0.8)
        except Exception as e:
            print(f"⚠️ [TwitterAPI 网络异常] 搜索抓取断开: {e}", flush=True)
            break
    return results



def fetch_reply_pages(tweet_id: str, max_pages: int = 2) -> list:
    reply_map = {}
    cursor = None
    for _ in range(max_pages):
        params = {"tweetId": tweet_id}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = requests.get(
                f"{BASE_URL}/twitter/tweet/replies",
                headers={"X-API-Key": TWITTERAPI_IO_KEY},
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
                rr = unify_schema(r)
                if rr["id"] and rr["id"] not in reply_map:
                    reply_map[rr["id"]] = rr
            cursor = data.get("next_cursor")
            if not data.get("has_next_page") or not cursor:
                break
            time.sleep(0.6)
        except Exception:
            break
    return sorted(reply_map.values(), key=lambda x: (x["likes"], x["replies"]), reverse=True)


# ==============================================================================
# 🧠 动态记忆库模块 (Memory Bank)
# ==============================================================================
MEMORY_FILE = Path("data/character_memory.json")


def load_memory():
    if MEMORY_FILE.exists():
        try:
            with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}



def save_memory(memory_data):
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(memory_data, f, ensure_ascii=False, indent=2)



def update_character_memory(parsed_data, today_str):
    memory = load_memory()
    count = 0
    for theme in parsed_data.get('themes', []):
        for tweet in theme.get('tweets', []):
            acc = tweet.get('account', '').lower().replace('@', '')
            content = tweet.get('content', '')
            if not acc or not content:
                continue
            if acc not in memory:
                memory[acc] = []
            new_entry = f"[{today_str}]: {content}"
            if new_entry not in memory[acc]:
                memory[acc].append(new_entry)
                memory[acc] = memory[acc][-5:]
                count += 1
    if count > 0:
        save_memory(memory)
        print(f"\n[Memory] 🧠 已更新 {count} 条历史记忆存入账本。", flush=True)


# ==============================================================================
# 🚀 xAI 大模型调用与 XML 提示词
# ==============================================================================

def build_xml_prompt(combined_jsonl: str, today_str: str, memory_context: str) -> str:
    return f"""
你是一位顶级的 AI 行业一级市场投资分析师及新媒体主编。
你的任务是基于提供的【一手推特数据】及大佬历史记忆，提炼出今日硅谷的【重大叙事动态】。

重要约束：
1. 主题只能来自 X/Twitter 一手内容，不得把外部新闻当作主题来源。
2. 资本与估值雷达、风险与中国视角这两个栏目请保留 XML 标签，但内部可以留空，由后续外部事实模块填充。
3. 输出必须是纯净 XML，不要解释，不要 Markdown，不要代码块。

【封面图生成指令】：
你必须为 <COVER> 标签生成一个高度定制化的 prompt 属性：
1. 严禁千篇一律地使用“赛博朋克、霓虹、紫色”。
2. 构图原则必须紧扣你为本次日报拟定的标题。
3. 提示词要求：100字左右的纯英文，包含具体的构图、材质、光影细节。

【核心任务：叙事挖掘】
不要做推文搬运工。请像研究员一样，从输入内容中分析出：
1. 哪些是正在产生的新叙事。
2. 哪些叙事发生了重大转向。
3. 哪些是原有叙事的深度推进。

【输出规模要求】
- 生成 4 到 6 个 <THEME> 模块。
- 挑选 6 到 10 条最具代表性的原始推文放入 <TOP_PICKS>。
- 每个 THEME 引用至少 1-2 条相关推文。

【输出结构规范】
<REPORT>
  <COVER title="10-20字爆款标题" prompt="英文提示词" insight="30字核心洞察"/>
  <PULSE>用一句话总结今日最核心的叙事流向。</PULSE>
  <THEMES>
    <THEME type="shift" emoji="⚔️">
      <TITLE>主题标题</TITLE>
      <NARRATIVE>叙事演变逻辑</NARRATIVE>
      <TWEET account="..." role="...">以中文为主精练原文，末尾附带真实互动数据（如 ❤️ 39190 | 💬 1904）</TWEET>
      <CONSENSUS>行业内已形成的最新共识</CONSENSUS>
      <DIVERGENCE>目前大佬们最激烈的争论点或未解之谜</DIVERGENCE>
    </THEME>
    <THEME type="new" emoji="🌱">
      <TITLE>主题标题</TITLE>
      <NARRATIVE>新叙事的内涵与底层问题</NARRATIVE>
      <TWEET account="..." role="...">...</TWEET>
      <OUTLOOK>未来 6-12 个月影响</OUTLOOK>
      <OPPORTUNITY>一级市场可能的机会</OPPORTUNITY>
      <RISK>潜在风险</RISK>
    </THEME>
  </THEMES>
  <INVESTMENT_RADAR>
  </INVESTMENT_RADAR>
  <RISK_CHINA_VIEW>
  </RISK_CHINA_VIEW>
  <TOP_PICKS>
    <TWEET account="..." role="...">流畅中文精译，末尾附带真实互动数据</TWEET>
  </TOP_PICKS>
</REPORT>

# 🧠 本期上榜大佬的近期历史记忆:
{memory_context if memory_context else "无历史记录"}

# X平台一手原始推文 (这是你的主要分析素材，请深入挖掘):
{combined_jsonl}

# 日期: {today_str}
"""



def llm_call_xai(combined_jsonl: str, today_str: str, memory_context: str) -> str:
    api_key = XAI_API_KEY.strip()
    if not api_key:
        print("❌ [xAI 报错] XAI_API_KEY 为空！", flush=True)
        return ""

    data = combined_jsonl[:100000] if len(combined_jsonl) > 100000 else combined_jsonl
    prompt = build_xml_prompt(data, today_str, memory_context)
    model_name = "grok-4.20-0309-reasoning"

    print(f"\n[xAI] Requesting {model_name} via Official SDK...", flush=True)
    print(f"[xAI] combined_jsonl chars={len(combined_jsonl)} | sent chars={len(data)} | memory chars={len(memory_context)} | prompt chars={len(prompt)}", flush=True)

    client = Client(api_key=api_key)
    for attempt in range(1, 4):
        try:
            print(f"[xAI] Attempt {attempt}/3", flush=True)
            chat = client.chat.create(model=model_name)
            chat.append(system("You are a professional analytical bot. You strictly output in XML format as instructed."))
            chat.append(user(prompt))
            result = chat.sample().content.strip()
            result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL | re.IGNORECASE).strip()
            result = re.sub(r'^`{3}(?:xml|jsonl|json)?\\n', '', result, flags=re.MULTILINE)
            result = re.sub(r'^`{3}\\n?', '', result, flags=re.MULTILINE)
            preview = result[:800].replace("\n", " ")
            print(f"[xAI] OK Response received ({len(result)} chars)", flush=True)
            print(f"[xAI] Preview: {preview}", flush=True)
            if '<TWEET' not in result:
                print("⚠️ [xAI] 原始返回中未发现 <TWEET> 标签。", flush=True)
            return result
        except Exception as e:
            print(f"⚠️ [xAI 异常] Attempt {attempt} failed: {e}", flush=True)
            print(traceback.format_exc(), flush=True)
            time.sleep(2 ** attempt)

    print("❌ [xAI 彻底失败] 所有重试均告失败。", flush=True)
    return ""

def parse_llm_xml(xml_text: str) -> dict:
    data = {
        "cover": {"title": "", "prompt": "", "insight": ""},
        "pulse": "",
        "themes": [],
        "investment_radar": [],
        "risk_china_view": [],
        "top_picks": []
    }
    if not xml_text:
        print("⚠️ [解析] xml_text 为空。", flush=True)
        return data

    def extract_tweets(block: str):
        tweets = []
        if not block:
            return tweets
        for t_match in re.finditer(r'<TWEET\b([^>]*)>(.*?)</TWEET>', block, re.IGNORECASE | re.DOTALL):
            attrs = t_match.group(1) or ""
            content = (t_match.group(2) or "").strip()
            account_m = re.search(r'account\s*=\s*[\'\"“”](.*?)[\'\"“”]', attrs, re.IGNORECASE | re.DOTALL)
            role_m = re.search(r'role\s*=\s*[\'\"“”](.*?)[\'\"“”]', attrs, re.IGNORECASE | re.DOTALL)
            tweets.append({
                "account": account_m.group(1).strip() if account_m else "",
                "role": role_m.group(1).strip() if role_m else "",
                "content": content,
            })
        return tweets

    cover_match = re.search(r'<COVER\s+title=[\'\"“”](.*?)[\'\"“”]\s+prompt=[\'\"“”](.*?)[\'\"“”]\s+insight=[\'\"“”](.*?)[\'\"“”]\s*/?>', xml_text, re.IGNORECASE | re.DOTALL)
    if not cover_match:
        cover_match = re.search(r'<COVER\s+title="(.*?)"\s+prompt="(.*?)"\s+insight="(.*?)"\s*/?>', xml_text, re.IGNORECASE | re.DOTALL)
    if cover_match:
        data["cover"] = {"title": cover_match.group(1).strip(), "prompt": cover_match.group(2).strip(), "insight": cover_match.group(3).strip()}

    pulse_match = re.search(r'<PULSE>(.*?)</PULSE>', xml_text, re.IGNORECASE | re.DOTALL)
    if pulse_match:
        data["pulse"] = pulse_match.group(1).strip()

    for theme_match in re.finditer(r'<THEME([^>]*)>(.*?)</THEME>', xml_text, re.IGNORECASE | re.DOTALL):
        attrs = theme_match.group(1)
        theme_body = theme_match.group(2)

        type_m = re.search(r'type\s*=\s*[\'\"“”](.*?)[\'\"“”]', attrs, re.IGNORECASE)
        emoji_m = re.search(r'emoji\s*=\s*[\'\"“”](.*?)[\'\"“”]', attrs, re.IGNORECASE)
        theme_type = type_m.group(1).strip().lower() if type_m else "shift"
        emoji = emoji_m.group(1).strip() if emoji_m else "🔥"

        t_tag = re.search(r'<TITLE>(.*?)</TITLE>', theme_body, re.IGNORECASE | re.DOTALL)
        theme_title = t_tag.group(1).strip() if t_tag else ""

        narrative_match = re.search(r'<NARRATIVE>(.*?)</NARRATIVE>', theme_body, re.IGNORECASE | re.DOTALL)
        narrative = narrative_match.group(1).strip() if narrative_match else ""

        tweets = extract_tweets(theme_body)

        con_match = re.search(r'<CONSENSUS>(.*?)</CONSENSUS>', theme_body, re.IGNORECASE | re.DOTALL)
        consensus = con_match.group(1).strip() if con_match else ""
        div_match = re.search(r'<DIVERGENCE>(.*?)</DIVERGENCE>', theme_body, re.IGNORECASE | re.DOTALL)
        divergence = div_match.group(1).strip() if div_match else ""

        out_match = re.search(r'<OUTLOOK>(.*?)</OUTLOOK>', theme_body, re.IGNORECASE | re.DOTALL)
        outlook = out_match.group(1).strip() if out_match else ""
        opp_match = re.search(r'<OPPORTUNITY>(.*?)</OPPORTUNITY>', theme_body, re.IGNORECASE | re.DOTALL)
        opportunity = opp_match.group(1).strip() if opp_match else ""
        risk_match = re.search(r'<RISK>(.*?)</RISK>', theme_body, re.IGNORECASE | re.DOTALL)
        risk = risk_match.group(1).strip() if risk_match else ""

        data["themes"].append({
            "type": theme_type,
            "emoji": emoji,
            "title": theme_title,
            "narrative": narrative,
            "tweets": tweets,
            "consensus": consensus,
            "divergence": divergence,
            "outlook": outlook,
            "opportunity": opportunity,
            "risk": risk
        })

    def extract_items(tag_name, target_list):
        block_match = re.search(rf'<{tag_name}>(.*?)</{tag_name}>', xml_text, re.IGNORECASE | re.DOTALL)
        if block_match:
            for item in re.finditer(r'<ITEM\s+category=[\'\"“”](.*?)[\'\"“”]>(.*?)</ITEM>', block_match.group(1), re.IGNORECASE | re.DOTALL):
                target_list.append({"category": item.group(1).strip(), "content": item.group(2).strip()})

    extract_items("INVESTMENT_RADAR", data["investment_radar"])
    extract_items("RISK_CHINA_VIEW", data["risk_china_view"])

    picks_match = re.search(r'<TOP_PICKS>(.*?)</TOP_PICKS>', xml_text, re.IGNORECASE | re.DOTALL)
    if picks_match:
        data["top_picks"] = extract_tweets(picks_match.group(1))

    total_theme_tweets = sum(len(theme.get("tweets", [])) for theme in data["themes"])
    print(f"[解析] themes={len(data['themes'])} | theme_tweets={total_theme_tweets} | top_picks={len(data['top_picks'])}", flush=True)
    for idx, theme in enumerate(data["themes"], start=1):
        print(f"[解析] Theme {idx}: {theme.get('title', '')} | tweets={len(theme.get('tweets', []))} | type={theme.get('type', '')}", flush=True)
    if total_theme_tweets == 0:
        print("⚠️ [解析警报] 主题存在，但所有 <THEME> 下的推文都没解析出来。请重点检查原始 XML 的 <TWEET> 属性格式。", flush=True)
    if not data["top_picks"]:
        print("⚠️ [解析警报] <TOP_PICKS> 为空或其中的 <TWEET> 未匹配成功。", flush=True)
    return data

def fetch_special_sections_with_perplexity(today_str: str):
    if not PPLX_API_KEY:
        return [], []

    prompt = f"""
你是 AI 行业日报中的外部事实栏目编辑。请只基于过去24小时内可验证的公开信息，输出严格 JSON，不要任何解释。

目标：生成两个栏目。
1. investment_radar：仅两个 item，类别必须是“投融资快讯”和“VC views”。
2. risk_china_view：仅两个 item，类别必须是“中国 AI 评价”和“中美AI博弈与衍生风险”。

要求：
- 每个 item 45-120 字中文。
- 强调事实更新，不写空泛长评论。
- 如果过去24小时没有明确新增，也要明确写“暂无重大新增”，但仍给一句最值得关注的事实背景。
- 中美AI博弈与衍生风险必须重点关注：出口限制、芯片/算力、模型生态、供应链重构、监管与地缘政治外溢风险。
- 只输出 JSON，格式如下：
{{
  "investment_radar": [
    {{"category": "投融资快讯", "content": "..."}},
    {{"category": "VC views", "content": "..."}}
  ],
  "risk_china_view": [
    {{"category": "中国 AI 评价", "content": "..."}},
    {{"category": "中美AI博弈与衍生风险", "content": "..."}}
  ]
}}

日期：{today_str}
"""

    try:
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {PPLX_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "sonar-pro",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.05
            },
            timeout=90,
        )
        if resp.status_code != 200:
            print(f"⚠️ [Perplexity 外部栏目报错] HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
            return [], []
        content = resp.json()["choices"][0]["message"]["content"].strip()
        investment_radar, risk_china_view = safe_parse_pplx_sections(content)
        if not investment_radar and not risk_china_view:
            print("⚠️ [Perplexity 外部栏目] 解析失败，已降级为空栏目。", flush=True)
            return [], []
        return investment_radar[:2], risk_china_view[:2]
    except Exception as e:
        print(f"⚠️ [Perplexity 外部栏目异常] {e}", flush=True)
        return [], []


# ==============================================================================
# 🚀 渲染与生图模块（保持原格式）
# ==============================================================================

def render_feishu_card(parsed_data: dict, today_str: str):
    webhooks = get_feishu_webhooks()
    if not webhooks:
        print("⚠️ [飞书] 未配置 webhook，跳过推送。", flush=True)
        return
    if not parsed_data.get("pulse"):
        print("⚠️ [飞书] pulse 为空，跳过推送。", flush=True)
        return

    theme_tweet_count = sum(len(theme.get("tweets", [])) for theme in parsed_data.get("themes", []))
    print(f"[飞书] 准备推送 | themes={len(parsed_data.get('themes', []))} | theme_tweets={theme_tweet_count} | top_picks={len(parsed_data.get('top_picks', []))}", flush=True)
    if theme_tweet_count == 0 and not parsed_data.get("top_picks"):
        print("⚠️ [飞书] 本次卡片没有任何推文内容，会只显示摘要栏目。", flush=True)

    elements = []
    elements.append({"tag": "markdown", "content": f"**▌ ⚡️ 今日看板 (The Pulse)**\n<font color='grey'>{parsed_data['pulse']}</font>"})
    elements.append({"tag": "hr"})

    if parsed_data["themes"]:
        elements.append({"tag": "markdown", "content": "**▌ 🧠 深度叙事追踪**"})
        for idx, theme in enumerate(parsed_data["themes"]):
            theme_md = f"**{theme['emoji']} {theme['title']}**\n"
            prefix = "🔭 新叙事观察" if theme.get("type") == "new" else "💡 叙事转向"
            theme_md += f"<font color='grey'>{prefix}：{theme['narrative']}</font>\n"
            for t in theme.get("tweets", []):
                account = t.get('account') or 'unknown'
                role = t.get('role') or 'unknown'
                content = t.get('content') or ''
                theme_md += f"🗣️ **@{account} | {role}**\n<font color='grey'>“{content}”</font>\n"
            if theme.get("type") == "new":
                if theme.get("outlook"):
                    theme_md += f"<font color='blue'>**🔮 解读与展望：**</font> {theme['outlook']}\n"
                if theme.get("opportunity"):
                    theme_md += f"<font color='green'>**🎯 潜在机会：**</font> {theme['opportunity']}\n"
                if theme.get("risk"):
                    theme_md += f"<font color='red'>**⚠️ 潜在风险：**</font> {theme['risk']}\n"
            else:
                if theme.get("consensus"):
                    theme_md += f"<font color='red'>**🔥 核心共识：**</font> {theme['consensus']}\n"
                if theme.get("divergence"):
                    theme_md += f"<font color='red'>**⚔️ 最大分歧：**</font> {theme['divergence']}\n"
            elements.append({"tag": "markdown", "content": theme_md.strip()})
            if idx < len(parsed_data["themes"]) - 1:
                elements.append({"tag": "hr"})
        elements.append({"tag": "hr"})

    def add_list_section(title, icon, items):
        if not items:
            return
        content = f"**▌ {icon} {title}**\n\n"
        for item in items:
            content += f"👉 **{item['category']}**：<font color='grey'>{item['content']}</font>\n"
        elements.append({"tag": "markdown", "content": content.strip()})
        elements.append({"tag": "hr"})

    add_list_section("资本与估值雷达", "💰", parsed_data["investment_radar"])
    add_list_section("风险与中国视角", "📊", parsed_data["risk_china_view"])

    if parsed_data["top_picks"]:
        picks_md = "**▌ 📣 今日精选推文 (Top 5 Picks)**\n"
        for t in parsed_data["top_picks"]:
            account = t.get('account') or 'unknown'
            role = t.get('role') or 'unknown'
            content = t.get('content') or ''
            picks_md += f"\n🗣️ **@{account} | {role}**\n<font color='grey'>\"{content}\"</font>\n"
        elements.append({"tag": "markdown", "content": picks_md.strip()})

    card_payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True, "enable_forward": True},
            "header": {"title": {"content": f"昨晚硅谷在聊啥 | {today_str}", "tag": "plain_text"}, "template": "blue"},
            "elements": elements + [{"tag": "note", "elements": [{"tag": "plain_text", "content": "Powered by TwitterAPI.io + xAI + Memory"}]}]
        }
    }
    for url in webhooks:
        try:
            resp = requests.post(url, json=card_payload, timeout=20)
            if resp.status_code == 200:
                print(f"✅ [飞书] 推送成功: {url.split('//')[-1][:18]}...", flush=True)
            else:
                print(f"⚠️ [飞书 Webhook 报错] 状态码: {resp.status_code}, 返回: {resp.text}", flush=True)
        except Exception as e:
            print(f"⚠️ [飞书网络异常] 推送断开: {e}", flush=True)

def render_wechat_html(parsed_data: dict, cover_url: str = "") -> str:
    html_lines = []
    if cover_url:
        html_lines.append(f'<p style="text-align:center;margin:0 0 16px 0;"><img src="{cover_url}" style="max-width:100%;border-radius:8px;" /></p>')

    def make_h3(title):
        return f'<h3 style="margin:24px 0 12px 0;font-size:18px;border-left:4px solid #4A90E2;padding-left:10px;color:#2c3e50;font-weight:bold;">{title}</h3>'

    def make_quote(content):
        return f'<div style="background:#f8f9fa;border-left:4px solid #8c98a4;padding:10px 14px;color:#555;font-size:15px;border-radius:0 4px 4px 0;margin:6px 0 10px 0;line-height:1.6;">{content}</div>'

    html_lines.append(make_h3("⚡️ 今日看板 (The Pulse)"))
    html_lines.append(make_quote(parsed_data.get('pulse', '')))

    if parsed_data["themes"]:
        html_lines.append(make_h3("🧠 深度叙事追踪"))
        for idx, theme in enumerate(parsed_data["themes"]):
            if idx > 0:
                html_lines.append('<hr style="border:none;border-top:1px solid #cbd5e1;margin:32px 0 24px 0;"/>')
            html_lines.append(f'<p style="font-weight:bold;font-size:16px;color:#1e293b;margin:16px 0 8px 0;">{theme["emoji"]} {theme["title"]}</p>')

            if theme.get("type") == "new":
                html_lines.append(f'<div style="background:#f4f8fb; padding:10px 12px; border-radius:6px; margin:0 0 8px 0; font-size:14px; color:#2c3e50;"><strong>🔭 新叙事观察：</strong>{theme["narrative"]}</div>')
            else:
                html_lines.append(f'<div style="background:#f4f8fb; padding:10px 12px; border-radius:6px; margin:0 0 8px 0; font-size:14px; color:#2c3e50;"><strong>💡 叙事转向：</strong>{theme["narrative"]}</div>')

            for t in theme["tweets"]:
                html_lines.append(f'<p style="margin:8px 0 2px 0;font-size:14px;font-weight:bold;color:#2c3e50;">🗣️ @{t["account"]} <span style="color:#94a3b8;font-weight:normal;">| {t["role"]}</span></p>')
                html_lines.append(make_quote(f'"{t["content"]}"'))

            if theme.get("type") == "new":
                if theme.get("outlook"):
                    html_lines.append(f'<p style="margin:6px 0; font-size:15px; line-height:1.6; background:#eef2ff; padding: 8px 12px; border-radius: 4px;"><strong style="color:#4f46e5;">🔮 解读与展望：</strong>{theme["outlook"]}</p>')
                if theme.get("opportunity"):
                    html_lines.append(f'<p style="margin:6px 0; font-size:15px; line-height:1.6; background:#f0fdf4; padding: 8px 12px; border-radius: 4px;"><strong style="color:#16a34a;">🎯 潜在机会：</strong>{theme["opportunity"]}</p>')
                if theme.get("risk"):
                    html_lines.append(f'<p style="margin:6px 0; font-size:15px; line-height:1.6; background:#fef2f2; padding: 8px 12px; border-radius: 4px;"><strong style="color:#dc2626;">⚠️ 潜在风险：</strong>{theme["risk"]}</p>')
            else:
                if theme.get("consensus"):
                    html_lines.append(f'<p style="margin:6px 0; font-size:15px; line-height:1.6; background:#fff5f5; padding: 8px 12px; border-radius: 4px;"><strong style="color:#d35400;">🔥 核心共识：</strong>{theme["consensus"]}</p>')
                if theme.get("divergence"):
                    html_lines.append(f'<p style="margin:6px 0; font-size:15px; line-height:1.6; background:#fff5f5; padding: 8px 12px; border-radius: 4px;"><strong style="color:#d35400;">⚔️ 最大分歧：</strong>{theme["divergence"]}</p>')

    def make_list_section(title, items):
        if not items:
            return
        html_lines.append(make_h3(title))
        for item in items:
            html_lines.append(f'<p style="margin:10px 0;font-size:15px;line-height:1.6;">👉 <strong style="color:#2c3e50;">{item["category"]}：</strong><span style="color:#333;">{item["content"]}</span></p>')

    make_list_section("💰 资本与估值雷达", parsed_data["investment_radar"])
    make_list_section("📊 风险与中国视角", parsed_data["risk_china_view"])

    if parsed_data["top_picks"]:
        html_lines.append(make_h3("📣 今日精选推文 (Top 5 Picks)"))
        for t in parsed_data["top_picks"]:
            html_lines.append(f'<p style="margin:12px 0 4px 0;font-size:14px;font-weight:bold;color:#2c3e50;">🗣️ @{t["account"]} <span style="color:#94a3b8;font-weight:normal;">| {t["role"]}</span></p>')
            html_lines.append(make_quote(f'"{t["content"]}"'))
    return "".join(html_lines)



def generate_cover_image(prompt):
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
            print("  🎨 硅基流动生图成功！", flush=True)
            return resp.json().get("images", [{}])[0].get("url") or resp.json().get("data", [{}])[0].get("url")
        print(f"  ⚠️ [SiliconFlow 生图报错] 状态码: {resp.status_code}, 详情: {resp.text}", flush=True)
    except Exception as e:
        print(f"  ⚠️ [SiliconFlow 网络异常] 生图请求断开: {e}", flush=True)
    return ""



def upload_to_imgbb_via_url(sf_url):
    if not IMGBB_API_KEY or not sf_url:
        return sf_url
    try:
        img_b64 = base64.b64encode(requests.get(sf_url, timeout=30).content).decode("utf-8")
        resp = requests.post(URL_IMGBB, data={"key": IMGBB_API_KEY, "image": img_b64}, timeout=45)
        if resp.status_code == 200:
            return resp.json()["data"]["url"]
        print(f"  ⚠️ [ImgBB 报错] 图床上传失败: {resp.text}", flush=True)
    except Exception as e:
        print(f"  ⚠️ [ImgBB 异常] 上传断开: {e}", flush=True)
    return sf_url



def push_to_wechat(html_content, title, cover_url=""):
    webhooks = get_wechat_webhooks()
    if not webhooks:
        return
    payload = {"title": title, "author": "Prinski", "html_content": html_content, "cover_jpg": cover_url}
    for url in webhooks:
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 200:
                print(f"  ✅ [微信推送成功] Sent to {url.split('//')[-1][:15]}...", flush=True)
            else:
                print(f"  ⚠️ [微信 Webhook 报错] 状态码 {resp.status_code}, 详情: {resp.text}", flush=True)
        except Exception as e:
            print(f"  ⚠️ [微信推送异常] 网络断开: {e}", flush=True)



def get_run_output_dir(today_str: str) -> Path:
    if TEST_MODE:
        now = datetime.now(timezone(timedelta(hours=8)))
        stamp = now.strftime("%H%M%S")
        suffix = uuid.uuid4().hex[:6]
        out_dir = Path(f"test_outputs/{today_str}/{stamp}_{suffix}")
    else:
        out_dir = Path(f"data/{today_str}")
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def write_json_file(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def zip_directory(source_dir: Path, zip_path: Path):
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in source_dir.rglob("*"):
            if file_path.is_file() and file_path != zip_path:
                zf.write(file_path, arcname=file_path.relative_to(source_dir))
    return zip_path


def try_upload_temp_bundle(zip_path: Path) -> str:
    try:
        with zip_path.open("rb") as f:
            resp = requests.post("https://0x0.st", files={"file": (zip_path.name, f, "application/zip")}, timeout=60)
        if resp.status_code == 200:
            return resp.text.strip()
        print(f"⚠️ [测试包上传失败] status={resp.status_code} body={resp.text[:300]}", flush=True)
    except Exception as e:
        print(f"⚠️ [测试包上传异常] {e}", flush=True)
    return ""


def save_test_sidecars(output_dir: Path, memory_candidates: list, parsed_data: dict, report_candidates: list):
    write_json_file(output_dir / "memory_candidates.json", memory_candidates)
    write_json_file(output_dir / "report_candidates.json", report_candidates)
    write_json_file(output_dir / "parsed_summary.json", {
        "theme_count": len(parsed_data.get("themes", [])),
        "theme_tweet_count": sum(len(t.get("tweets", [])) for t in parsed_data.get("themes", [])),
        "top_picks_count": len(parsed_data.get("top_picks", [])),
        "theme_titles": [t.get("title", "") for t in parsed_data.get("themes", [])],
    })


def save_daily_data(today_str: str, post_objects: list, report_text: str, parsed_data: dict = None, raw_llm_xml: str = "", extra_meta: dict = None):
    output_dir = get_run_output_dir(today_str)
    combined_path = output_dir / "combined.txt"
    daily_report_path = output_dir / "daily_report.txt"
    raw_xml_path = output_dir / "raw_llm_report.xml"
    parsed_json_path = output_dir / "parsed_data.json"
    meta_path = output_dir / "run_meta.json"

    combined_path.write_text("\n".join(json.dumps(obj, ensure_ascii=False) for obj in post_objects), encoding="utf-8")
    final_report = build_report_xml(parsed_data) if parsed_data else report_text
    if final_report:
        daily_report_path.write_text(final_report, encoding="utf-8")
    if raw_llm_xml:
        raw_xml_path.write_text(raw_llm_xml, encoding="utf-8")
    if parsed_data is not None:
        write_json_file(parsed_json_path, parsed_data)

    meta = extra_meta or {}
    meta.update({
        "test_mode": TEST_MODE,
        "output_dir": str(output_dir),
        "combined_count": len(post_objects),
        "theme_count": len(parsed_data.get("themes", [])) if parsed_data else 0,
        "theme_tweet_count": sum(len(t.get("tweets", [])) for t in parsed_data.get("themes", [])) if parsed_data else 0,
        "top_picks_count": len(parsed_data.get("top_picks", [])) if parsed_data else 0,
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
    })
    write_json_file(meta_path, meta)

    print(f"[落盘] 输出目录: {output_dir}", flush=True)
    print(f"[落盘] combined.txt => {combined_path}", flush=True)
    print(f"[落盘] daily_report.txt => {daily_report_path}", flush=True)
    if raw_llm_xml:
        print(f"[落盘] raw_llm_report.xml => {raw_xml_path}", flush=True)
    if parsed_data is not None:
        print(f"[落盘] parsed_data.json => {parsed_json_path}", flush=True)

    if TEST_MODE:
        zip_path = output_dir / "test_artifacts.zip"
        zip_directory(output_dir, zip_path)
        print(f"[测试模式] 临时打包完成: {zip_path}", flush=True)
        temp_url = try_upload_temp_bundle(zip_path)
        if temp_url:
            print(f"[测试模式] 临时下载链接: {temp_url}", flush=True)
        else:
            print("[测试模式] 临时下载链接生成失败；请直接从日志中的 runner 路径取文件。", flush=True)

    return output_dir

def save_memory_snapshot(today_str: str, top_feed: list):
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    snapshot_file = data_dir / f"memory_{today_str}.json"
    serializable = []
    for t in top_feed:
        serializable.append({
            "author": t.get("author", ""),
            "score": t.get("score", 0),
            "likes": t.get("likes", 0),
            "replies": t.get("replies", 0),
            "quotes": t.get("quotes", 0),
            "text": t.get("text", ""),
            "deep_replies": [
                {"author": r.get("author", ""), "likes": r.get("likes", 0), "text": r.get("text", "")}
                for r in t.get("deep_replies", [])
            ]
        })
    snapshot_file.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")



def update_account_stats(final_feed: list, parsed_data: dict):
    stats_file = Path("data/account_stats.json")
    stats = {}
    if stats_file.exists():
        try:
            stats = json.loads(stats_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    today_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    used_accounts = set()
    for theme in parsed_data.get("themes", []):
        for t in theme.get("tweets", []):
            used_accounts.add(t.get("account", "").lower())
    for t in parsed_data.get("top_picks", []):
        used_accounts.add(t.get("account", "").lower())

    touched_today = set()
    for t in final_feed:
        acc = t.get("a", "unknown").lower()
        if acc not in stats:
            stats[acc] = {"fetched_days": 0, "total_tweets": 0, "used_in_reports": 0, "last_active": ""}
        if acc not in touched_today and stats[acc].get("last_active") != today_str:
            stats[acc]["fetched_days"] += 1
            touched_today.add(acc)
        stats[acc]["total_tweets"] += 1
        stats[acc]["last_active"] = today_str

    for acc in used_accounts:
        acc_clean = acc.replace("@", "")
        if acc_clean in stats:
            stats[acc_clean]["used_in_reports"] += 1

    stats_file.parent.mkdir(parents=True, exist_ok=True)
    stats_file.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")


# ==============================================================================
# 🚀 MAIN 入口
# ==============================================================================


import os

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
    "robots", "coding", "codegen", "rag", "retrieval", "openclaw", "codex"
}

NON_AI_HOT_NOISE = {
    "tesla", "fsd", "spacex", "starlink", "trump", "election", "ukraine",
    "immigration", "border", "tariff"
}

TOXIC_PATTERNS = [
    r"\bpedo\b", r"\bidiot\b", r"\bstupid\b", r"\bfuck\b", r"\bwtf\b",
    r"\bscam\b", r"\bracist\b", r"\btrash\b", r"\bgarbage\b"
]

PPLX_API_KEY = os.getenv("PERPLEXITY_API_KEY") or os.getenv("PPLXAPIKEY") or ""


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def is_target_account(acc: str) -> bool:
    return (acc or "").lower().replace("@", "") in TARGET_SET


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


def filter_deep_replies(replies: list) -> list:
    clean = []
    seen = set()
    for r in replies or []:
        text = norm_text(r.get("text", ""))
        likes = int(r.get("likes", 0) or 0)
        author = (r.get("author", "") or "").lower()
        if likes < MIN_REPLY_LIKES:
            continue
        if looks_toxic_or_empty(text):
            continue
        key = (author, text[:160].lower())
        if key in seen:
            continue
        seen.add(key)
        clean.append({
            "author": r.get("author", ""),
            "likes": likes,
            "text": text,
        })
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


def score_and_filter(posts: list) -> list:
    seen = set()
    cleaned = []
    for t in posts or []:
        tid = str(t.get("id", "")).strip()
        text = norm_text(t.get("text", ""))
        if not tid or not text or tid in seen:
            continue
        seen.add(tid)
        likes = int(t.get("likes", 0) or 0)
        replies = int(t.get("replies", 0) or 0)
        quotes = int(t.get("quotes", 0) or 0)
        base_score = likes + replies * 2 + quotes * 3
        total_score = base_score + apply_ai_relevance(t)
        if total_score < 300:
            continue
        t["score"] = round(total_score, 2)
        t["source_type"] = "target" if is_target_account(t.get("author", "")) else "echo"
        cleaned.append(t)
    cleaned.sort(key=lambda x: x.get("score", 0), reverse=True)
    return cleaned


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
        risk_categories = {x["category"] for x in risk_china_view}
        if "中国 AI 最新动态" not in risk_categories or "中美 AI 博弈与衍生风险" not in risk_categories:
            risk_china_view = default_risk_china_view
        return investment_radar, risk_china_view
    except Exception as e:
        print(f"⚠️ [Perplexity] 请求失败，使用默认结构：{e}", flush=True)
        return default_investment_radar, default_risk_china_view


def build_memory_candidates(parsed_data: dict) -> list:
    candidates = []
    for theme in parsed_data.get("themes", []):
        title = norm_text(theme.get("title", ""))
        consensus = norm_text(theme.get("consensus", ""))
        divergence = norm_text(theme.get("divergence", ""))
        for t in theme.get("tweets", []):
            account = (t.get("account", "") or "").replace("@", "").lower()
            content = norm_text(t.get("content", ""))
            if not account or not content:
                continue
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
    snapshot_file = data_dir / f"memory_{today_str}.json"
    snapshot_file.write_text(json.dumps(memory_candidates, ensure_ascii=False, indent=2), encoding="utf-8")


def update_character_memory(parsed_data: dict, today_str: str):
    memory_file = Path("data/character_memory.json")
    memory = {}
    if memory_file.exists():
        try:
            memory = json.loads(memory_file.read_text(encoding="utf-8"))
        except Exception:
            memory = {}

    new_items = {}
    for theme in parsed_data.get("themes", []):
        title = norm_text(theme.get("title", ""))
        for t in theme.get("tweets", []):
            acc = (t.get("account", "") or "").replace("@", "").lower()
            content = norm_text(t.get("content", ""))
            if not acc or not content:
                continue
            line = f"[{today_str}] {title}: {content}"
            new_items.setdefault(acc, []).append(line)
    for t in parsed_data.get("top_picks", []):
        acc = (t.get("account", "") or "").replace("@", "").lower()
        content = norm_text(t.get("content", ""))
        if not acc or not content:
            continue
        line = f"[{today_str}] TOP_PICK: {content}"
        new_items.setdefault(acc, []).append(line)

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


def _load_stats_file(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
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
    today_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
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


def xml_escape(s: str) -> str:
    s = str(s or "")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def build_report_xml(parsed_data: dict) -> str:
    lines = ["<REPORT>"]
    cover = parsed_data.get("cover", {}) or {}
    lines.append(
        f'  <COVER title="{xml_escape(cover.get("title", ""))}" prompt="{xml_escape(cover.get("prompt", ""))}" insight="{xml_escape(cover.get("insight", ""))}"/>'
    )
    lines.append(f"  <PULSE>{xml_escape(parsed_data.get('pulse', ''))}</PULSE>")
    lines.append("  <THEMES>")
    for theme in parsed_data.get("themes", []):
        lines.append(f'    <THEME type="{xml_escape(theme.get("type", "shift"))}" emoji="{xml_escape(theme.get("emoji", "📌"))}">')
        lines.append(f"      <TITLE>{xml_escape(theme.get('title', ''))}</TITLE>")
        lines.append(f"      <NARRATIVE>{xml_escape(theme.get('narrative', ''))}</NARRATIVE>")
        for t in theme.get("tweets", []):
            lines.append(f'      <TWEET account="{xml_escape(t.get("account", ""))}" role="{xml_escape(t.get("role", ""))}">{xml_escape(t.get("content", ""))}</TWEET>')
        if theme.get("consensus"):
            lines.append(f"      <CONSENSUS>{xml_escape(theme.get('consensus', ''))}</CONSENSUS>")
        if theme.get("divergence"):
            lines.append(f"      <DIVERGENCE>{xml_escape(theme.get('divergence', ''))}</DIVERGENCE>")
        if theme.get("outlook"):
            lines.append(f"      <OUTLOOK>{xml_escape(theme.get('outlook', ''))}</OUTLOOK>")
        if theme.get("opportunity"):
            lines.append(f"      <OPPORTUNITY>{xml_escape(theme.get('opportunity', ''))}</OPPORTUNITY>")
        if theme.get("risk"):
            lines.append(f"      <RISK>{xml_escape(theme.get('risk', ''))}</RISK>")
        lines.append("    </THEME>")
    lines.append("  </THEMES>")
    lines.append("  <INVESTMENT_RADAR>")
    for item in parsed_data.get("investment_radar", []):
        lines.append(f'    <ITEM category="{xml_escape(item.get("category", ""))}">{xml_escape(item.get("content", ""))}</ITEM>')
    lines.append("  </INVESTMENT_RADAR>")
    lines.append("  <RISK_CHINA_VIEW>")
    for item in parsed_data.get("risk_china_view", []):
        lines.append(f'    <ITEM category="{xml_escape(item.get("category", ""))}">{xml_escape(item.get("content", ""))}</ITEM>')
    lines.append("  </RISK_CHINA_VIEW>")
    lines.append("  <TOP_PICKS>")
    for t in parsed_data.get("top_picks", []):
        lines.append(f'    <TWEET account="{xml_escape(t.get("account", ""))}" role="{xml_escape(t.get("role", ""))}">{xml_escape(t.get("content", ""))}</TWEET>')
    lines.append("  </TOP_PICKS>")
    lines.append("</REPORT>")
    return "\n".join(lines)


def save_daily_data(today_str: str, post_objects: list, report_text: str, parsed_data: dict = None):
    data_dir = Path(f"data/{today_str}")
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "combined.txt").write_text("\n".join(json.dumps(obj, ensure_ascii=False) for obj in post_objects), encoding="utf-8")
    final_report = build_report_xml(parsed_data) if parsed_data else report_text
    if final_report:
        (data_dir / "daily_report.txt").write_text(final_report, encoding="utf-8")


def main():
    print("=" * 60, flush=True)
    print("昨晚硅谷在聊啥 v16.0 (分层清洗 + 记忆收敛 + 统计拆池版)", flush=True)
    print("=" * 60, flush=True)
    print(f"[模式] TEST_MODE={TEST_MODE}", flush=True)

    if not TWITTERAPI_IO_KEY or not TARGET_SET:
        print("❌ 错误: 未配置 API KEY 或本地 txt 名单为空", flush=True)
        return

    today_str, _ = get_dates()
    print(f"🚀 开始抓取 {len(TARGET_SET)} 位核心节点的最新动态...", flush=True)

    raw_feed = []
    acc_list = list(TARGET_SET)
    batch_size = 10 if TEST_MODE else 12

    for i in range(0, len(acc_list), batch_size):
        chunk = acc_list[i:i + batch_size]
        q1 = "(" + " OR ".join([f"from:{a}" for a in chunk]) + f") since:{SINCE_DATE_STR} -filter:retweets"
        q2 = "(" + " OR ".join([f"@{a}" for a in chunk]) + f") since:{SINCE_DATE_STR} min_faves:20 -filter:replies"
        original = fetch_advanced_search_pages(q1, query_type="Latest", max_pages=2)
        echoes = fetch_advanced_search_pages(q2, query_type="Top", max_pages=2)
        raw_feed.extend(original)
        raw_feed.extend(echoes)
        print(f"✅ chunk {i // batch_size + 1}: 原创 {len(original)} 条 | 外部回响 {len(echoes)} 条", flush=True)
        time.sleep(1.0)

    if not raw_feed:
        print("❌ [终极警告] 本次运行未能从推特获取任何有效数据！程序强行终止。", flush=True)
        return

    print(f"[抓取] raw_feed={len(raw_feed)}", flush=True)
    clean_feed = score_and_filter(raw_feed)
    print(f"[筛选] clean_feed={len(clean_feed)}", flush=True)

    tier_1 = clean_feed[:15]
    tier_2 = clean_feed[15:REPORT_POOL_LIMIT]

    print(f"\n[深挖] 正在为 Tier 1 (Top {len(tier_1)}) 高分话题抓取高质量回复...", flush=True)
    for t in tier_1:
        raw_replies = fetch_reply_pages(t["id"], max_pages=2)
        t["deep_replies"] = filter_deep_replies(raw_replies)
        print(f"[深挖] @{t['author']} | tweet_id={t['id']} | raw_replies={len(raw_replies)} | kept={len(t['deep_replies'])}", flush=True)
        time.sleep(0.6)

    report_candidates = []
    for t in tier_1:
        reply_strs = [f"[高质量回复 @{r['author']}]: {r['text'][:180]} (❤️ {r['likes']})" for r in t.get("deep_replies", [])]
        s_text = t["text"] + ("\n\n" + "\n".join(reply_strs) if reply_strs else "")
        report_candidates.append({
            "a": t["author"],
            "tweet_id": t["id"],
            "l": t["likes"],
            "r": t["replies"],
            "score": t["score"],
            "t": t["created_ts"],
            "source_type": t.get("source_type", "unknown"),
            "s": s_text,
        })
    for t in tier_2:
        report_candidates.append({
            "a": t["author"],
            "tweet_id": t["id"],
            "l": t["likes"],
            "r": t["replies"],
            "score": t["score"],
            "t": t["created_ts"],
            "source_type": t.get("source_type", "unknown"),
            "s": t["text"],
        })

    combined_jsonl = "\n".join(json.dumps(obj, ensure_ascii=False) for obj in report_candidates)
    if not combined_jsonl.strip():
        print("❌ [终极警告] 最终送入 LLM 的内容为空！", flush=True)
        return

    print(f"[LLM 输入] report_candidates={len(report_candidates)} | combined_jsonl chars={len(combined_jsonl)}", flush=True)
    today_accounts = set(t.get("a", "").lower() for t in report_candidates)
    memory = load_memory()
    memory_context_lines = []
    for acc in today_accounts:
        if acc in memory and memory[acc]:
            memory_context_lines.append(f"@{acc} 近期观点:\n- " + "\n- ".join(memory[acc]))
    memory_context = "\n\n".join(memory_context_lines)
    print(f"[记忆] accounts_with_memory={len(memory_context_lines)} | memory chars={len(memory_context)}", flush=True)

    xml_result = llm_call_xai(combined_jsonl, today_str, memory_context)
    if not xml_result:
        print("❌ [终极警告] LLM 处理失败，无报告输出！", flush=True)
        return

    parsed_data = parse_llm_xml(xml_result)
    default_investment_radar, default_risk_china_view = default_special_sections()
    parsed_data["investment_radar"] = default_investment_radar
    parsed_data["risk_china_view"] = default_risk_china_view

    investment_radar, risk_china_view = fetch_special_sections_with_perplexity(today_str)
    if investment_radar and isinstance(investment_radar, list):
        parsed_data["investment_radar"] = investment_radar
    if risk_china_view and isinstance(risk_china_view, list):
        parsed_data["risk_china_view"] = risk_china_view

    memory_candidates = build_memory_candidates(parsed_data)
    if TEST_MODE:
        print("[测试模式] 跳过 update_character_memory/save_memory_snapshot/update_account_stats 的正式写库流程。", flush=True)
    else:
        update_character_memory(parsed_data, today_str)

    cover_url = ""
    if parsed_data.get("cover", {}).get("prompt"):
        print(f"\n[生图] 提取到生图提示词: {parsed_data['cover']['prompt'][:50]}...", flush=True)
        sf_url = generate_cover_image(parsed_data["cover"]["prompt"])
        cover_url = upload_to_imgbb_via_url(sf_url) if sf_url else ""
        print(f"[生图] cover_url={'OK' if cover_url else 'EMPTY'}", flush=True)
    else:
        print("\n⚠️ [渲染警报] 未能从 Grok 报告中解析出生图 prompt 属性！", flush=True)

    output_dir = save_daily_data(
        today_str,
        report_candidates,
        xml_result,
        parsed_data,
        raw_llm_xml=xml_result,
        extra_meta={
            "cover_url": cover_url,
            "memory_candidates_count": len(memory_candidates),
        },
    )

    if TEST_MODE:
        save_test_sidecars(output_dir, memory_candidates, parsed_data, report_candidates)
        zip_path = output_dir / "test_artifacts.zip"
        if not zip_path.exists():
            zip_directory(output_dir, zip_path)
        temp_url = try_upload_temp_bundle(zip_path)
        if temp_url:
            print(f"[测试模式] 复核下载链接: {temp_url}", flush=True)
    else:
        save_memory_snapshot(today_str, memory_candidates)
        update_account_stats(report_candidates, parsed_data)

    render_feishu_card(parsed_data, today_str)

    wechat_hooks = get_wechat_webhooks()
    if wechat_hooks:
        html_content = render_wechat_html(parsed_data, cover_url)
        push_to_wechat(
            html_content,
            title=f"{parsed_data.get('cover', {}).get('title') or '今日核心动态'} | 昨晚硅谷在聊啥",
            cover_url=cover_url,
        )

    print(f"[完成] 输出目录: {output_dir}", flush=True)
    print("\n🎉 V16.0 全链路执行完毕！", flush=True)

if __name__ == "__main__":
    main()
