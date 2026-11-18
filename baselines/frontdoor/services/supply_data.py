INSTRUCTIONS = (
    "You are a synthetic Supply risk specialist. Analyze only local product evidence. "
    "Calculate demand coverage and supplier delays, return risk, mitigations and source IDs. "
    "Do not mutate orders or infer live supplier information."
)
SUPPLY_DATA = {
    "P-100": {"available": 80, "expected_delivery": 40, "demand": 150, "delay_days": 3},
    "P-200": {"available": 240, "expected_delivery": 100, "demand": 100, "delay_days": 0},
}
