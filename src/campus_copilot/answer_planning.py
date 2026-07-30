from typing import Dict


GENERAL_GUIDANCE_TERMS = (
    "就业方向", "就业前景", "职业方向", "能做什么工作", "毕业后做什么",
    "行业方向", "专业前景",
)
CURRENT_OFFICIAL_TERMS = (
    "今年", "本届", "最新", "现在", "什么时候", "截止", "通知", "名额",
    "申请条件", "申请流程", "怎么办理", "怎么申请",
)
HISTORICAL_OUTCOME_TERMS = (
    "多少人", "多少名", "百分之", "比例", "大概几个", "排到多少", "往届",
)
EXPERIENCE_TERMS = (
    "宿舍", "食堂", "外卖", "快递", "生活区", "床", "社团", "好吃", "方便吗",
)


def classify_question(query: str) -> str:
    compact = query.replace(" ", "").lower()
    if any(term in compact for term in GENERAL_GUIDANCE_TERMS):
        return "general_guidance"
    if any(term in compact for term in HISTORICAL_OUTCOME_TERMS):
        return "historical_outcome"
    if any(term in compact for term in CURRENT_OFFICIAL_TERMS):
        return "current_official"
    if any(term in compact for term in EXPERIENCE_TERMS):
        return "campus_experience"
    return "campus_fact"


def build_answer_plan(query: str, answerability: str) -> Dict[str, object]:
    question_type = classify_question(query)
    has_local_answer = answerability in {
        "supported",
        "supported_with_context",
        "supported_freshness_unverified",
        "mixed_sources_review_required",
        "experience_only",
    }
    if has_local_answer:
        fallback_route = "local_knowledge"
    elif question_type == "general_guidance":
        fallback_route = "public_web_discovery"
    else:
        fallback_route = "official_web_discovery"

    guidance = {
        "historical_outcome": (
            "Answer with the observed cohort result first. Explicitly distinguish an observed "
            "outcome from a fixed official quota."
        ),
        "current_official": (
            "Answer the current rule or action first. Preserve dates and say when the latest "
            "official notice still needs checking."
        ),
        "campus_experience": (
            "Answer like a knowledgeable student ambassador. Label experience naturally and "
            "mention that arrangements can change."
        ),
        "general_guidance": (
            "Separate general career information from school-specific claims. Use public-web "
            "sources only through the discovery route."
        ),
        "campus_fact": "Give a concise direct answer, then one useful qualification if needed.",
    }[question_type]
    return {
        "question_type": question_type,
        "fallback_route": fallback_route,
        "response_shape": "direct_answer_then_context_then_sources",
        "max_answer_paragraphs": 3,
        "guidance": guidance,
        "web_search_executed": False,
    }


def fallback_message(route: str) -> str:
    if route == "public_web_discovery":
        return (
            "这个问题更适合结合公开行业资料回答。目前本轮还没有执行联网检索，"
            "所以我先不凭模型记忆下结论。下一步应检索可信的专业介绍、招聘和行业来源。"
        )
    if route == "official_web_discovery":
        return (
            "现有知识库还不能直接回答这个问题。下一步应优先查询中国传媒大学、"
            "海南国际学院及其官方微信公众号的最新发布，再给你一个有出处的答案。"
        )
    return "现有知识库还不能直接回答这个问题，我先不把相似材料当成答案。"
