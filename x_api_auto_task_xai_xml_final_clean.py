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
