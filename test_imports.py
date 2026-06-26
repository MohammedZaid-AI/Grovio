modules = [

    "ai.intelligence.memory",
    "ai.intelligence.supplier_memory",
    "ai.intelligence.pattern_detector",
    "ai.intelligence.price_tracker",

    "ai.agents.procurement_forecaster",
    "ai.agents.ai_coo",

    "ai.reports.daily_brief",

]

for module in modules:

    try:

        __import__(module)

        print(f"✅ {module}")

    except Exception as e:

        print(f"❌ {module}")

        print(e)