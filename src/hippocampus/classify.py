import re
from hippocampus.types import ClassificationResult

HEAVY_CATEGORIES = {"architectural", "security", "compliance", "cost", "domain"}


def classify(description: str) -> ClassificationResult:
    lower = description.lower()

    if re.search(r"\b(variable name|file name|formatting|split function|helper location)\b", lower):
        return ClassificationResult(weight="skip", category=None, reason="Implementation-level detail")

    if re.search(r"\b(skip for now|revisit later|post-mvp|hold off|consciously not deciding|not deciding yet|choosing not to decide)\b", lower):
        return ClassificationResult(weight="deferred", category=None, reason="Explicit deferral detected")

    category = "architectural"

    if re.search(r"\b(auth|authentication|authoriz|encrypt|secret|credential|trust|permission)\b", lower):
        category = "security"
    elif re.search(r"\b(gdpr|compliance|legal|regulation|residency|retention|policy)\b", lower):
        category = "compliance"
    elif re.search(r"\b(cost|budget|license|pricing|build vs buy)\b", lower):
        category = "cost"
    elif re.search(r"\b(schema|migration|database|storage|table|index|data model)\b", lower):
        category = "data"
    elif re.search(r"\b(api|rest|graphql|endpoint|contract|versioning|interface)\b", lower):
        category = "api"
    elif re.search(r"\b(performance|cache|latency|throughput|async|scale)\b", lower):
        category = "performance"
    elif re.search(r"\b(package|dependency|library|pip|npm|yarn|upgrade|remove)\b", lower):
        category = "dependency"
    elif re.search(r"\b(test|coverage|e2e|unit|integration|mock)\b", lower):
        category = "testing"
    elif re.search(r"\b(error|exception|retry|fallback|alert|log)\b", lower):
        category = "error-handling"
    elif re.search(r"\b(state|client state|server state|cache invalidation)\b", lower):
        category = "state"
    elif re.search(r"\b(naming|convention|ubiquitous language)\b", lower):
        category = "naming"
    elif re.search(r"\b(deploy|observability|rollback|monitoring|ci|cd|prod)\b", lower):
        category = "operational"
    elif re.search(r"\b(aggregate|entity|domain model|bounded context)\b", lower):
        category = "domain"
    elif re.search(r"\b(team|owner|ownership|responsibility)\b", lower):
        category = "team"
    elif re.search(r"\b(ux|ui|user flow|interaction|product)\b", lower):
        category = "ux-product"

    is_heavy = category in HEAVY_CATEGORIES or bool(
        re.search(r"\b(multiple services|significant rework|irreversible|compliance|legal|cost commitment|serious mistake)\b", lower)
    )

    weight = "heavy" if is_heavy else "standard"
    return ClassificationResult(weight=weight, category=category, reason=f"Auto-classified as {category} ({weight})")
