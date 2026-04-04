import os
import glob
import json
import requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

# ==========================================
# 1. 渠道配置
# ==========================================
FEISHU_MAIN_URL = os.getenv("FEISHU_WEBHOOK_URL_1")
FEISHU_TEST_URL = os.getenv("FEISHU_WEBHOOK_URL")
JIJYUN_URL = os.getenv("JIJYUN_WEBHOOK_URL")
TEST_MODE = os.getenv("TEST_MODE_ENV", "false").lower() == "true"

WINDOW_DAYS = 15
MAX_CHURN = 3
MIN_DROP_SCORE = 140
MIN_PROMOTE_SCORE = 180
MIN_PROMOTE_ACTIVE_DAYS = 2


def normalize(name):
    return name.replace("@", "").strip().lower()



def read_name_file(filename):
    if not os.path.exists(filename):
        return set()
    with open(filename, "r", encoding="utf-8") as f:
        return {normalize(line) for line in f if line.strip() and not line.strip().startswith("#")}



def push_to_channels(content):
    if not content.strip():
        return
    webhook_url = FEISHU_TEST_URL if TEST_MODE else FEISHU_MAIN_URL
    if webhook_url:
        payload = {"msg_type": "post", "content": {"post": {"zh_cn": {
            "title": "⚖️ 硅谷情报局：半月度名单自动换血报告",
            "content": [[{"tag": "text", "text": content}]]
        }}}}
        requests.post(webhook_url, json=payload, timeout=15)
    if JIJYUN_URL:
        requests.post(JIJYUN_URL, json={"content": content}, timeout=15)


# ==========================================
# 2. 数据读取
# ==========================================

def load_account_stats():
    stats_file = Path("data/account_stats.json")
    if not stats_file.exists():
        return {}
    try:
        return json.loads(stats_file.read_text(encoding="utf-8"))
    except Exception:
        return {}



def load_recent_memory(window_days):
    past_days = datetime.now(timezone.utc) - timedelta(days=window_days)
    memory_files = glob.glob("data/memory_*.json")

    external_scores = defaultdict(float)
    external_days = defaultdict(set)
    valid_files_count = 0

    for file_path in memory_files:
        try:
            date_str = Path(file_path).stem.split("_")[-1]
            file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if file_date < past_days:
                continue

            valid_files_count += 1
            day_key = file_date.strftime("%Y-%m-%d")
            with open(file_path, "r", encoding="utf-8") as f:
                daily_data = json.load(f)

            for t in daily_data:
                author = normalize(t.get("author", ""))
                score = float(t.get("score", 0) or 0)
                if author:
                    external_scores[author] += score
                    external_days[author].add(day_key)

                for r in t.get("deep_replies", []):
                    r_author = normalize(r.get("author", ""))
                    if r_author:
                        external_scores[r_author] += float(r.get("likes", 0) or 0) * 1.5
                        external_days[r_author].add(day_key)
        except Exception as e:
            print(f"⚠️ 跳过无法解析的文件 {file_path}: {e}")

    return external_scores, external_days, valid_files_count


# ==========================================
# 3. 排名逻辑
# ==========================================

def build_internal_rank(experts, stats):
    ranked = []
    for acc in experts:
        st = stats.get(acc, {})
        fetched_days = int(st.get("fetched_days", 0) or 0)
        used_in_reports = int(st.get("used_in_reports", 0) or 0)
        total_tweets = int(st.get("total_tweets", 0) or 0)
        score = total_tweets * 1.0 + fetched_days * 20 + used_in_reports * 120
        ranked.append({
            "account": acc,
            "score": round(score, 1),
            "fetched_days": fetched_days,
            "used_in_reports": used_in_reports,
            "total_tweets": total_tweets,
        })
    ranked.sort(key=lambda x: x["score"])
    return ranked



def build_external_rank(current_all, external_scores, external_days):
    ranked = []
    for acc, score in external_scores.items():
        if acc in current_all:
            continue
        active_days = len(external_days.get(acc, set()))
        blended = float(score) + active_days * 40
        ranked.append({
            "account": acc,
            "score": round(blended, 1),
            "active_days": active_days,
            "raw_score": round(float(score), 1),
        })
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked



def write_experts_file(experts):
    with open("experts.txt", "w", encoding="utf-8") as f:
        f.write("# 硅谷情报局动态专家名单 (15日自动更新)\n")
        for exp in sorted(experts):
            f.write(f"{exp}\n")


# ==========================================
# 4. 主流程
# ==========================================

def main():
    print("🔍 启动半月度名单自动洗牌程序...")

    whales = read_name_file("whales.txt")
    experts = read_name_file("experts.txt")
    current_all = whales | experts

    if not experts:
        print("❌ 未找到 experts.txt，跳过维护。")
        return

    stats = load_account_stats()
    external_scores, external_days, valid_files_count = load_recent_memory(WINDOW_DAYS)
    print(f"📊 共分析了 {valid_files_count} 天的历史候选数据。")

    internal_rank = build_internal_rank(experts, stats)
    external_rank = build_external_rank(current_all, external_scores, external_days)

    drop_candidates = [x for x in internal_rank if x["score"] < MIN_DROP_SCORE][:MAX_CHURN]
    promote_candidates = [
        x for x in external_rank
        if x["score"] >= MIN_PROMOTE_SCORE and x["active_days"] >= MIN_PROMOTE_ACTIVE_DAYS
    ][:len(drop_candidates)]

    churn_n = min(len(drop_candidates), len(promote_candidates))
    dropped = drop_candidates[:churn_n]
    promoted = promote_candidates[:churn_n]

    new_experts = set(experts)
    for item in dropped:
        new_experts.discard(item["account"])
    for item in promoted:
        new_experts.add(item["account"])

    if churn_n > 0:
        write_experts_file(new_experts)
        report = "🔄 15日周期名单自动洗牌已完成！\n\n"
        report += "📉 【末位淘汰】\n"
        for item in dropped:
            report += f"  ❌ @{item['account']} (综合分 {item['score']}，抓取天数 {item['fetched_days']}，入选报告 {item['used_in_reports']} 次，已移出专家池)\n"

        report += "\n📈 【新贵晋升】\n"
        for item in promoted:
            report += f"  ✨ @{item['account']} (候选综合分 {item['score']}，活跃天数 {item['active_days']}，野生捕获贡献分 {item['raw_score']}，已收编)\n"

        report += f"\n🎯 当前监控底座总人数已更新为: {len(whales) + len(new_experts)} 人。"
    else:
        report = "🔄 15日周期核查完毕。现有专家在抓取覆盖、入选报告次数和历史活跃度上整体稳定，本周期无符合条件的淘汰与晋升账号，名单保持不变。"

    print(report)
    push_to_channels(report)


if __name__ == "__main__":
    main()
