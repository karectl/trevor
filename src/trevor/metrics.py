"""Custom Prometheus counters for trevor domain events."""

from prometheus_client import Counter

requests_submitted_total = Counter(
    "trevor_requests_submitted_total",
    "Total submitted airlock requests",
    ["direction"],
)

requests_approved_total = Counter(
    "trevor_requests_approved_total",
    "Total approved airlock requests",
    ["direction"],
)

requests_rejected_total = Counter(
    "trevor_requests_rejected_total",
    "Total rejected airlock requests",
    ["direction"],
)

agent_reviews_total = Counter(
    "trevor_agent_reviews_total",
    "Total agent reviews completed",
)

objects_uploaded_total = Counter(
    "trevor_objects_uploaded_total",
    "Total output objects uploaded",
    ["direction"],
)
